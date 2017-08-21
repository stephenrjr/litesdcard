#!/usr/bin/env python3
# -*- coding: utf-8 -*-

from fractions import Fraction

from litex.gen import *
from litex.gen.genlib.resetsync import AsyncResetSynchronizer

from litex.build.generic_platform import *

from litex.soc.cores.uart import UARTWishboneBridge

from litex.soc.integration.soc_core import *
from litex.soc.integration.builder import *

from litesdcard.phy.sdphy import SDPHY, SDCtrl
from litesdcard.frontend.ram import RAMReader, RAMWriter, RAMWrAddr
from litesdcard.core.downc import Stream32to8
from litesdcard.core.upc import Stream8to32

from litex.boards.platforms import papilio_pro

_sd_io = [
    ("sdcard", 0,
        Subsignal("data", Pins("P48 P66 P61 P58")),
        Subsignal("cmd", Pins("P51"), Misc("PULLUP")),
        Subsignal("clk", Pins("P56")),
        Subsignal("cd", Pins("P67")),
        IOStandard("LVCMOS33"), Misc("SLEW=FAST"),
    )
]

class _CRG(Module):
    def __init__(self, platform, clk_freq):
        self.clock_domains.cd_sys = ClockDomain()
        self.clock_domains.cd_sys_ps = ClockDomain()

        f0 = 32*1000000
        clk32 = platform.request("clk32")
        clk32a = Signal()
        self.specials += Instance("IBUFG", i_I=clk32, o_O=clk32a)
        clk32b = Signal()
        self.specials += Instance("BUFIO2", p_DIVIDE=1,
                                  p_DIVIDE_BYPASS="TRUE", p_I_INVERT="FALSE",
                                  i_I=clk32a, o_DIVCLK=clk32b)
        f = Fraction(int(clk_freq), int(f0))
        n, m, p = f.denominator, f.numerator, 16
        assert f0/n*m == clk_freq
        pll_lckd = Signal()
        pll_fb = Signal()
        pll = Signal(6)
        self.specials.pll = Instance("PLL_ADV", p_SIM_DEVICE="SPARTAN6",
                                     p_BANDWIDTH="OPTIMIZED", p_COMPENSATION="INTERNAL",
                                     p_REF_JITTER=.01, p_CLK_FEEDBACK="CLKFBOUT",
                                     i_DADDR=0, i_DCLK=0, i_DEN=0, i_DI=0, i_DWE=0, i_RST=0, i_REL=0,
                                     p_DIVCLK_DIVIDE=1, p_CLKFBOUT_MULT=m*p//n, p_CLKFBOUT_PHASE=0.,
                                     i_CLKIN1=clk32b, i_CLKIN2=0, i_CLKINSEL=1,
                                     p_CLKIN1_PERIOD=1000000000/f0, p_CLKIN2_PERIOD=0.,
                                     i_CLKFBIN=pll_fb, o_CLKFBOUT=pll_fb, o_LOCKED=pll_lckd,
                                     o_CLKOUT0=pll[0], p_CLKOUT0_DUTY_CYCLE=.5,
                                     o_CLKOUT1=pll[1], p_CLKOUT1_DUTY_CYCLE=.5,
                                     o_CLKOUT2=pll[2], p_CLKOUT2_DUTY_CYCLE=.5,
                                     o_CLKOUT3=pll[3], p_CLKOUT3_DUTY_CYCLE=.5,
                                     o_CLKOUT4=pll[4], p_CLKOUT4_DUTY_CYCLE=.5,
                                     o_CLKOUT5=pll[5], p_CLKOUT5_DUTY_CYCLE=.5,
                                     p_CLKOUT0_PHASE=0., p_CLKOUT0_DIVIDE=p//1,
                                     p_CLKOUT1_PHASE=0., p_CLKOUT1_DIVIDE=p//1,
                                     p_CLKOUT2_PHASE=0., p_CLKOUT2_DIVIDE=p//1,
                                     p_CLKOUT3_PHASE=0., p_CLKOUT3_DIVIDE=p//1,
                                     p_CLKOUT4_PHASE=0., p_CLKOUT4_DIVIDE=p//1,  # sys
                                     p_CLKOUT5_PHASE=270., p_CLKOUT5_DIVIDE=p//1,  # sys_ps
        )
        self.specials += Instance("BUFG", i_I=pll[4], o_O=self.cd_sys.clk)
        self.specials += Instance("BUFG", i_I=pll[5], o_O=self.cd_sys_ps.clk)
        self.specials += AsyncResetSynchronizer(self.cd_sys, ~pll_lckd)

        self.specials += Instance("ODDR2", p_DDR_ALIGNMENT="NONE",
                                  p_INIT=0, p_SRTYPE="SYNC",
                                  i_D0=0, i_D1=1, i_S=0, i_R=0, i_CE=1,
                                  i_C0=self.cd_sys.clk, i_C1=~self.cd_sys.clk,
                                  o_Q=platform.request("sdram_clock"))

class SDSoC(SoCCore):
    default_platform = "papilio_pro"
    csr_map = {
        "sdphy":     20,
        "sdctrl":    21,
        "ramreader": 22,
        "ramwraddr": 23
    }
    csr_map.update(SoCCore.csr_map)

    def __init__(self, **kwargs):
        platform = papilio_pro.Platform()
        platform.add_extension(_sd_io)
        clk_freq = 50*1000000
        SoCCore.__init__(self, platform,
                         clk_freq=clk_freq,
                         cpu_type=None,
                         csr_data_width=32,
                         with_uart=False,
                         with_timer=False,
                         ident="SDCard Test SoC",
                         integrated_sram_size=1024,
                         **kwargs)

        self.submodules.crg = _CRG(platform, clk_freq)

        self.add_cpu_or_bridge(UARTWishboneBridge(platform.request("serial"), clk_freq, baudrate=115200))
        self.add_wb_master(self.cpu_or_bridge.wishbone)

        self.submodules.sdphy = SDPHY(platform.request('sdcard'), platform.device)
        self.submodules.sdctrl = SDCtrl()

        self.submodules.ramreader = RAMReader()
        self.submodules.ramwriter = RAMWriter()
        self.submodules.ramwraddr = RAMWrAddr()
        self.add_wb_master(self.ramreader.bus)
        self.add_wb_master(self.ramwriter.bus)

        self.submodules.stream32to8 = Stream32to8()
        self.submodules.stream8to32 = Stream8to32()

        self.comb += [
            self.sdctrl.source.connect(self.sdphy.sink),
            self.sdphy.source.connect(self.sdctrl.sink),

            self.sdctrl.rsource.connect(self.stream8to32.sink),
            self.stream8to32.source.connect(self.ramwraddr.sink),
            self.ramwraddr.source.connect(self.ramwriter.sink),

            self.ramreader.source.connect(self.stream32to8.sink),
            self.stream32to8.source.connect(self.sdctrl.rsink),
        ]

def main():
    soc = SDSoC()
    builder = Builder(soc, csr_csv="../../litesdcard/software/csr.csv")
    builder.build()

if __name__ == "__main__":
    main()