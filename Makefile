# 启动网页桌面、构建桌面镜像和运行检查。

.PHONY: all serve images image-desktop check help

all: serve

serve:
	./bin/claude-desktop

images: image-desktop

image-desktop:
	python3 -c 'from webapp.engine import Engine; Engine(on_log=print).build()'

# 后端功能测试，以及 Python 和 Shell 语法检查。
check:
	python3 -m unittest discover -s tests -v
	python3 -m py_compile webapp/*.py desktop/*.py
	sh -n bin/claude-desktop
	for script in desktop/*.sh; do bash -n "$$script" || exit; done
	@echo "检查通过"

help:
	@echo "make [all] / serve  启动本地桌面网页控制台"
	@echo "make image-desktop  构建 Debian / Xfce / Claude Desktop 镜像"
	@echo "make images         同 make image-desktop"
	@echo "make check          运行后端测试和语法检查"
