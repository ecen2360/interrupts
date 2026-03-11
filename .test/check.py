import sys
from util import nios2_as, get_debug, require_symbols, hotpatch, get_regs
from csim import Nios2

asm = sys.stdin.read()

obj = nios2_as(asm.encode('utf-8'))
cpu = Nios2(obj=obj)

print(get_debug(cpu))
