# SPDX-License-Identifier: GPL-2.0
# Out-of-tree build: make [KDIR=<kernel build dir>] [MO=<output dir, must exist>]
# MO is Kbuild's external-module output dir; it reaches the kernel make via MAKEFLAGS.
KDIR ?= /usr/src/kernels/$(shell uname -r)

all:
	$(MAKE) -C $(KDIR) M=$(CURDIR) modules

clean:
	$(MAKE) -C $(KDIR) M=$(CURDIR) clean

.PHONY: all clean
