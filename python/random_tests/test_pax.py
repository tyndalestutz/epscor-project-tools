import os

fd = os.open("/dev/usbtmc0", os.O_WRONLY)
os.write(fd, b"*IDN?\n")
os.close(fd)

fd = os.open("/dev/usbtmc0", os.O_RDONLY)
print(os.read(fd, 256))
os.close(fd)
