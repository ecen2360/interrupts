
import sys
import subprocess
elf_name = sys.argv[1]

# First, objcopy to get full memory dump
mem_f = tempfile.NamedTemporaryFile()
p = subprocess.Popen(['bin/nios2-elf-objcopy', '-O', 'binary', \
                        '--gap-fill', '0x00', \
                        elf_name, mem_f],
                    stdout=subprocess.PIPE, stderr=subprocess.PIPE)
if p.wait() != 0:
    print('objcopy error: ', p.stderr.read())
    sys.exit(-1)




{"prog": "0001883a003da03a44434241484746454c4b4a49504f004d0057565554", "symbols": {"_start": 0, "FOO": 8, "TOTAL": 25}}

