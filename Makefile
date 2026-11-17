# 启动网页桌面、构建镜像或安装 Claude Code 命令行工具。

PREFIX  ?= /usr/local
BINDIR  ?= $(PREFIX)/bin
INSTALL ?= install

# 默认安装到系统目录；安装到用户目录可使用 make PREFIX=$$HOME/.local SUDO=。
SUDO ?= sudo

# 仅用于旧版 Claude Code 镜像；网页桌面使用 desktop/Containerfile。
# make legacy-images CONTAINERFILE=examples/Containerfile.rust-nightly
# make legacy-images CONTAINERFILE= 可使用原始基础镜像。
CONTAINERFILE ?= $(wildcard Containerfile)
export CONTAINERFILE

.PHONY: all serve install install-claude uninstall images legacy-images image-desktop image-claude check help

all: serve

serve:
	./bin/claude-desktop

install: install-claude

install-claude: bin/claude
	$(SUDO) $(INSTALL) -D -m 755 bin/claude $(BINDIR)/claude-podman
	@echo "已安装 $(BINDIR)/claude-podman"

uninstall:
	$(SUDO) rm -f $(BINDIR)/claude-podman

images: image-desktop

legacy-images: image-claude

image-desktop:
	python3 -c 'from webapp.engine import Engine; Engine(on_log=print).build()'

image-claude:
	./devops/build-claude.sh

# 后端功能测试，以及 Python 和 Shell 语法检查。
check:
	python3 -m unittest discover -s tests -v
	python3 -m py_compile webapp/*.py desktop/*.py
	sh -n bin/claude-desktop
	for script in desktop/*.sh; do bash -n "$$script" || exit; done
	bash -n bin/claude
	sh -n devops/base-image.sh
	sh -n devops/build-claude.sh
	@echo "检查通过"

help:
	@echo "make [all] / serve  启动本地桌面网页控制台"
	@echo "make image-desktop  构建 Debian / Xfce / Claude Desktop 镜像"
	@echo "make images         同 make image-desktop"
	@echo "make legacy-images  构建旧版 Claude Code 命令行镜像"
	@echo "make image-claude   同 make legacy-images"
	@echo "make install        安装 Claude Code 启动器到 $(BINDIR)"
	@echo "make install-claude 同 make install"
	@echo "make uninstall      从 $(BINDIR) 移除 Claude Code 启动器"
	@echo "make check          运行后端测试和语法检查"
	@echo ""
	@echo "安装变量：PREFIX（默认 /usr/local）、BINDIR、SUDO（留空可跳过 sudo）"
	@echo "旧版镜像变量：CONTAINERFILE、BASE_IMAGE、NO_CACHE=1"
