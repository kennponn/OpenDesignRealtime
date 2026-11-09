# GitHub 发布检查

个人使用目录和 Git 仓库必须分开。本源码目录可作为仓库根目录，父目录的 `data` 不应加入版本控制。

目标仓库：[kennponn/OpenDesignRealtime](https://github.com/kennponn/OpenDesignRealtime)。
Windows 包通过 GitHub Releases 发布，不把 EXE 或私人使用目录提交到源码仓库。

## 发布前

1. 确认仓库所有者、仓库名及公开 / 私有权限。首次建议先检查内容，再设为公开。
2. 检查 `git status --short`、`git ls-files` 及暂存区差异。
3. 只提交源代码、测试、构建脚本、说明和虚构账号示例图。
4. 不要上传真实使用目录、数据库、浏览器配置、真实截图、CSV 或私人诊断文件。
5. 运行 `test.ps1`，再运行 `build.ps1`；构建 Release 时只打包 EXE 和公开说明。
6. 解压 Release 到全新空目录测试，确认启动后没有任何账号。
7. 核对 Release ZIP 的完整文件列表，确保没有 `data` 或浏览器配置。

## 推荐文件

- Git 仓库：本目录的源代码、测试、构建配置及说明。
- Windows Release：`OpenDesignCards.exe`、`README.md`、`SECURITY.md`、校验文件。
- 示例图：`assets/preview.png`，仅含虚构数据。

发布包与源码 ZIP 不包含 Git 认证信息。连接 GitHub 请使用 GitHub 授权或 CLI 登录，不要把 Token 写进源码、远程地址或脚本。

公开仓库前还应确定许可证。当前未附加许可证，也没有声明未经确认的商业再分发权利。
