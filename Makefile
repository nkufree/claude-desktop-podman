# Start the browser desktop, build its image, or install the legacy CLI tools.

PREFIX  ?= /usr/local
BINDIR  ?= $(PREFIX)/bin
INSTALL ?= install

# /usr/local/bin is root-owned, so the copy is elevated. Override with
# `make SUDO=` when running as root or installing into a prefix you own,
# e.g. `make PREFIX=$$HOME/.local SUDO=`.
SUDO ?= sudo

# A Containerfile of your own to layer under both images: ./Containerfile
# (gitignored) if it exists, or name one, e.g.
# `make legacy-images CONTAINERFILE=examples/Containerfile.rust-nightly`.
# `make legacy-images CONTAINERFILE=` builds the stock CLI images. CI runs the build
# scripts directly, so it never picks this up.
CONTAINERFILE ?= $(wildcard Containerfile)
export CONTAINERFILE

.PHONY: all serve install install-claude install-codex uninstall images legacy-images image-desktop image-claude image-codex check help

all: serve

serve:
	./bin/claude-desktop

install: install-claude install-codex

install-claude: bin/claude
	$(SUDO) $(INSTALL) -D -m 755 bin/claude $(BINDIR)/claude-podman
	@echo "installed $(BINDIR)/claude-podman"

install-codex: bin/codex
	$(SUDO) $(INSTALL) -D -m 755 bin/codex $(BINDIR)/codex-podman
	@echo "installed $(BINDIR)/codex-podman"

uninstall:
	$(SUDO) rm -f $(BINDIR)/claude-podman $(BINDIR)/codex-podman

images: image-desktop

legacy-images: image-claude image-codex

image-desktop:
	python3 -c 'from webapp.engine import Engine; Engine(on_log=print).build()'

image-claude:
	./devops/build-claude.sh

image-codex:
	./devops/build-codex.sh

# Functional backend checks plus parse-only checks for shell and Python.
check:
	python3 -m unittest discover -s tests -v
	python3 -m py_compile webapp/*.py desktop/*.py
	sh -n bin/claude-desktop
	for script in desktop/*.sh; do bash -n "$$script" || exit; done
	bash -n bin/claude
	bash -n bin/codex
	sh -n devops/base-image.sh
	sh -n devops/build-claude.sh
	sh -n devops/build-codex.sh
	@echo "ok"

help:
	@echo "make [all] / serve  start the local desktop web console"
	@echo "make image-desktop  build the Debian / Xfce / Claude Desktop image"
	@echo "make legacy-images  build both legacy CLI images"
	@echo "make install        install legacy CLI launchers into $(BINDIR)"
	@echo "make install-claude install claude-podman only"
	@echo "make install-codex  install codex-podman only"
	@echo "make uninstall      remove both from $(BINDIR)"
	@echo "make images         build the desktop image"
	@echo "make image-claude   build the claude-code image only"
	@echo "make image-codex    build the codex image only"
	@echo "make check          run backend tests and syntax checks"
	@echo ""
	@echo "Variables: PREFIX (default /usr/local), BINDIR, SUDO (set empty to skip sudo)"
	@echo "Images:    CONTAINERFILE (default ./Containerfile if present), BASE_IMAGE, NO_CACHE=1"
