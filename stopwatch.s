# This section is placed at address 0x00000000
.section .reset, "ax"
    br      _start

# This section is placed at address 0x00000020
.section .exceptions, "ax"
    eret

.text
_start:
    nop
