from util import nios2_as, get_debug, require_symbols, hotpatch, get_regs
from csim import Nios2
import sys


# convert number to what we'd expect on hex display
def hexd(n):
    DIGIT = [0x3f, 0x06, 0x5b, 0x4f, 0x66, 0x6d, 0x7d, 0x07, 0x7f, 0x67]
    n_digits = [int(x) for x in '%d'%n]     # individual digits
    n_bytes = list(map(lambda x: DIGIT[x], n_digits))   # map to 7seg values
    return struct.unpack('>I', bytes(n_bytes).rjust(4, b'\x00'))[0]       # combine into 32-bit value


def seg_to_rows(seg_byte):
    """Convert a 7-segment byte to 3 rows of ASCII art (4 chars wide each).

    Bit layout:
      bit 0 = a (top)        bit 4 = e (bottom-left)
      bit 1 = b (top-right)  bit 5 = f (top-left)
      bit 2 = c (bot-right)  bit 6 = g (middle)
      bit 3 = d (bottom)     bit 7 = dp (decimal point, bottom-right)
    """
    a  = (seg_byte >> 0) & 1
    b  = (seg_byte >> 1) & 1
    c  = (seg_byte >> 2) & 1
    d  = (seg_byte >> 3) & 1
    e  = (seg_byte >> 4) & 1
    f  = (seg_byte >> 5) & 1
    g  = (seg_byte >> 6) & 1
    dp = (seg_byte >> 7) & 1
    return [
        (' _ ' if a else '   ') + ' ',
        ('|' if f else ' ') + ('_' if g else ' ') + ('|' if b else ' ') + ' ',
        ('|' if e else ' ') + ('_' if d else ' ') + ('|' if c else ' ') + ('.' if dp else ' '),
    ]

def display_ascii(val32):
    """Return 3 lines of ASCII art for a 4-digit 7-seg display value.

    The 32-bit word maps as: bits 31:24 -> HEX3 (leftmost) ... bits 7:0 -> HEX0 (rightmost).
    """
    segs = [(val32 >> (8 * i)) & 0xff for i in range(3, -1, -1)]
    digit_rows = [seg_to_rows(s) for s in segs]
    return [''.join(d[r] for d in digit_rows) for r in range(3)]


class StopwatchTest(object):

    class Timer(object):
        def __init__(self, on_change=None):
            self.running = False
            self.cont = False
            self.to = 0
            self.control_val = 0
            self.period = 0
            self.snapshot = 0
            self.on_change = on_change

        def status(self, val=None):
            if val is None:
                return (self.is_running() << 1) | self.to 
            # Writing clears TO bit (bit 0) for any bit set in val
            if (val & 1) == 0:
                self.to = 0
            if self.on_change:
                self.on_change()
            return 0

        def control(self, val=None):
            if val is None:
                return self.control_val
            self.control_val = val
            stop = self.control_val & 0x8
            start = self.control_val & 0x4
            cont = self.control_val & 0x2
            
            if start != 0:
                self.running = True
            if stop != 0:
                self.running = False
            self.cont = (cont != 0)
            return 0

        def count_low(self, val=None):
            if val is None:
                return self.period & 0xFFFF
            self.period = (self.period & 0xFFFF0000) | (val & 0xFFFF)
            return 0

        def count_high(self, val=None):
            if val is None:
                return (self.period >> 16) & 0xFFFF
            self.period = (self.period & 0xFFFF) | ((val & 0xFFFF) << 16)
            return 0

        def snapshot_low(self, val=None):
            if val is not None:
                # A write to this address triggers the capture
                self.snapshot = self.period
            return self.snapshot & 0xFFFF

        def snapshot_high(self, val=None):
            return (self.snapshot >> 16) & 0xFFFF

        def is_running(self):
            return self.running


    class Button(object):
        def __init__(self, on_change=None):
            self.edge_val = 0
            self.mask_val = 0
            self.on_change = on_change

        def data(self, val=None):
            return 0

        def interrupt_mask(self, val=None):
            if val is None:
                return self.mask_val
            self.mask_val = val
            return 0

        def edge(self, val=None):
            if val is None:
                return self.edge_val
            # Writing clears the bits specified
            self.edge_val &= ~val
            if self.on_change:
                self.on_change()
            return 0


    class HexDisplay(object):
        def __init__(self):
            self.lower = 0  # HEX3-HEX0
            self.upper = 0  # HEX5-HEX4

        def lower_reg(self, val=None):
            if val is not None:
                self.lower = val
            return self.lower

        def upper_reg(self, val=None):
            if val is not None:
                self.upper = val
            return self.upper


    def __init__(self, cpu, verbose=False):
        self.passed = True
        self.cpu = cpu
        self.verbose = verbose
        self.timer = self.Timer(on_change=self.update_pending)
        self.button = self.Button(on_change=self.update_pending)
        self.hex = self.HexDisplay()

        self.cpu.reset()
        self.cpu.add_mmio(0xFF202000, self.timer.status)
        self.cpu.add_mmio(0xFF202004, self.timer.control)
        self.cpu.add_mmio(0xFF202008, self.timer.count_low)
        self.cpu.add_mmio(0xFF20200C, self.timer.count_high)
        self.cpu.add_mmio(0xFF202010, self.timer.snapshot_low)
        self.cpu.add_mmio(0xFF202014, self.timer.snapshot_high)

        self.cpu.add_mmio(0xFF200050, self.button.data)
        self.cpu.add_mmio(0xFF200058, self.button.interrupt_mask)
        self.cpu.add_mmio(0xFF20005C, self.button.edge)

        self.cpu.add_mmio(0xFF200020, self.hex.lower_reg)
        self.cpu.add_mmio(0xFF200030, self.hex.upper_reg)

    def __del__(self):
        self.timer.on_change = None
        self.button.on_change = None

    def update_pending(self):
        """Recompute ipending based on current peripheral and CPU state."""
        ipending = self.cpu.get_ctl_reg(4)

        # Timer IRQ0: TO bit set, ITO enabled in timer control
        if self.timer.to & 0x1 and self.timer.control_val & 0x1:
            ipending |= 0x1
        else:
            ipending &= ~0x1

        # Button IRQ1: any unmasked edge capture bits set
        if self.button.edge_val & self.button.mask_val:
            ipending |= 0x2
        else:
            ipending &= ~0x2

        self.cpu.set_ctl_reg(4, ipending)

    def run(self, limit=10000):
        instrs = self.cpu.run_until_halted(limit)
        if instrs == limit:
            self.cpu.unhalt()

    def _pie_set(self):
        return bool(self.cpu.get_ctl_reg(0) & 0x1)  # ctl0 status bit 0

    def _irq_enabled(self, irq_bit):
        return bool(self.cpu.get_ctl_reg(3) & (1 << irq_bit))  # ctl3 ienable

    def _timer_interrupt_ready(self):
        return (self._irq_enabled(0) and
                bool(self.timer.control_val & 0x1))  # ITO bit in timer control

    def _button_interrupt_ready(self, button_mask):
        return (self._irq_enabled(1) and
                bool(self.button.mask_val & button_mask))  # button interrupt mask

    def fire_timer(self, expect_running=True):
        """Simulate a timer timeout and deliver the interrupt to the CPU if enabled."""

        if not self.timer.is_running():
            if expect_running:
                print('Error: timer not setup/running')
                print('status: %08x' % self.cpu.get_ctl_reg(0))
                print('ienable: %08x' % self.cpu.get_ctl_reg(3))
                print('ipending: %08x' % self.cpu.get_ctl_reg(4))
                print('Timer status reg: %08x' % self.timer.status())
                print('Timer ctl reg: %08x' % self.timer.control_val)
            return

        self.timer.period = 0
        self.timer.to |= 1
        if self._timer_interrupt_ready():
            self.update_pending()
        else:
            print('Error: timer not ready for interrupt')
            print('status: %08x' % self.cpu.get_ctl_reg(0))
            print('ienable: %08x' % self.cpu.get_ctl_reg(3))
            print('ipending: %08x' % self.cpu.get_ctl_reg(4))
            print('Timer ctl reg: %08x' % self.timer.control_val)
            sys.exit(-1)
        self.run()

    def push_button(self, button0=False, button1=False):
        """Simulate a button press and deliver the interrupt to the CPU if enabled."""
        mask = (0x1 if button0 else 0) | (0x2 if button1 else 0)
        self.button.edge_val |= mask
        if self._button_interrupt_ready(mask):
            self.update_pending()
            #self.cpu.interrupt()
        else:
            print('Error: button not ready for interrupt')
            print('status: %08x' % self.cpu.get_ctl_reg(0))
            print('ienable: %08x' % self.cpu.get_ctl_reg(3))
            print('ipending: %08x' % self.cpu.get_ctl_reg(4))
            print('Timer ctl reg: %08x' % self.timer.control_val)
            sys.exit(-1)
        self.run()

    def push_button0(self):
        self.push_button(button0=True)

    def push_button1(self):
        self.push_button(button1=True)

    def push_both_buttons(self):
        self.push_button(button0=True, button1=True)

    def fire_timer_and_push_button(self, button0=False, button1=False):
        """Simulate a timer timeout and button press occurring simultaneously."""
        self.timer.period = 0
        self.timer.to |= 1
        mask = (0x1 if button0 else 0) | (0x2 if button1 else 0)
        self.button.edge_val |= mask
        self.update_pending()
        if self._timer_interrupt_ready() or self._button_interrupt_ready(mask):
            #self.cpu.interrupt()
            pass
        self.run()

    def read_hex(self):
        """Return (lower, upper) raw hex display register values."""
        return (self.hex.lower, self.hex.upper)

    def check_hex(self, val, note=""):
        """Verify the hex display shows the given stopwatch value (e.g. 123 = 1 second, 23 hundredths).
        HEX3-HEX2 show the integer (seconds), HEX1-HEX0 show the hundredths, dp on HEX2.
        """
        DIGIT = [0x3f, 0x06, 0x5b, 0x4f, 0x66, 0x6d, 0x7d, 0x07, 0x7f, 0x67]
        int_part = val // 100
        frac_part = val % 100
        s_tens = DIGIT[(int_part % 100) // 10]
        s_ones = DIGIT[int_part % 10]
        c_tens = DIGIT[frac_part // 10]
        c_ones = DIGIT[frac_part % 10]
        # HEX3=s_tens, HEX2=s_ones|dp, HEX1=c_tens, HEX0=c_ones
        expected = (s_tens << 24) | ((s_ones | 0x80) << 16) | (c_tens << 8) | c_ones
        actual = self.read_hex()[0]
        act_hex3 = (actual >> 24) & 0xff
        # HEX3 (leading digit) may be blank (0x00) when the expected digit is '0' (0x3f)
        hex3_ok = (act_hex3 == s_tens) or (s_tens == 0x3f and act_hex3 == 0x00)
        ok = hex3_ok and (actual & 0x00ffffff) == (expected & 0x00ffffff)
        if self.verbose:
            print('%s: %.2f' % (note, val/100),)
            print('\n'.join(display_ascii(self.read_hex()[0])))
            print('')
        if not ok:
            print('check_hex(%.2f): %s FAIL' % (val/100, note))
            print('  expected: 0x%08x      actual: 0x%08x' % (expected, actual))
            for exp_line, act_line in zip(display_ascii(expected), display_ascii(actual)):
                print('   %s     %s' % (exp_line, act_line))
            print('')
            sys.exit(-1)



def check_stopwatch(asm, debug=False):

    obj = nios2_as(asm.encode('utf-8'))
    r = require_symbols(obj, ['_start'])
    if r is not None:
        print('Error: failed to assemble')
        return (False, r, "")

    cpu = Nios2(obj=obj)
    test = StopwatchTest(cpu, verbose=debug)

    # Run CPU briefly for initialization (sets up ienable, starts timer, etc.)
    test.run()

    # TODO: check that setup is correct?

    t = 0
    test.check_hex(t, "initial zero value")


    # Simulate timer interrupts
    for i in range(1234):
        test.fire_timer()
        t += 1
        test.check_hex(t, "in initial timer loop")

    # Simulate a button press 
    # STOP Button
    test.push_button1()  # stop

    test.check_hex(t, "after STOP pressed")

    for i in range(22):
        test.fire_timer(expect_running=False)

    test.check_hex(t, "Ran for some timer loops after STOP")   # check it didn't advance any
    # START button
    test.push_button1() # start

    for i in range(50):
        test.fire_timer()
        t += 1
        test.check_hex(t, "after resuming")


    test.push_button1() # stop

    test.check_hex(t, "after STOP second time")

    test.push_button0() # reset

    for i in range(11):
        test.fire_timer(expect_running=False)

    t = 0
    test.check_hex(t, "after reset")

    test.push_button1() # start

    for i in range(50):
        test.fire_timer()
        t += 1
        test.check_hex(t, "after START again")

    test.push_button1() # stop

    test.push_both_buttons() # start / reset
    t = 0

    for i in range(50):
        test.fire_timer()
        t += 1
        test.check_hex(t, "after pressing START/RESET simultaneously")


    print('Passed all tests')


debug = False
if len(sys.argv) > 1:
    debug = True
check_stopwatch(sys.stdin.read(), debug)
