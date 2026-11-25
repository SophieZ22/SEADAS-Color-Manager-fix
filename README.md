# SeaDAS Color Manager：RGB 重建后失效的本地补丁

修复一种可重复出现的界面问题：**对 RGB 图像执行 equalize 后，关闭 RGB，再次合成 RGB，Color Manager 持续显示没有选中图像，无法继续调色；重启 SeaDAS 后暂时恢复。**

本项目提供补丁源码、按本机原始模块构建的工具，以及带校验和原文件备份的安装/回退流程。首次安装时构建本机补丁；软件升级后，从升级后的原始模块重新评估和构建。

> 已验证范围：2026-11-25 本机 Windows 11、SeaDAS `VERSION.txt = 12.0.0`、SNAP RCP 模块 `14.0.1`、Java 21.0.8。完整兼容性以原始 JAR 的 SHA256 为准。其他版本尚未验证。此补丁是本地修复，不是 NASA 官方发行版。

## 1. 问题表现与复现

1. 打开影像并合成 RGB。本次原影像使用 645 / 555 / 469 nm，补丁本身不限定这些波段。
2. 在 Color Manager 中把 Histogram matching 设置为 `Equalize`。
3. 关闭 RGB 图像窗口。
4. 再次合成 RGB。
5. Color Manager 提示没有选中图像，无法调整显示。故障发生后，重新点击图像、切换单波段图、关闭再打开 Color Manager 均不能恢复；重启应用可以暂时恢复。

这是一种已观察到的复现路径，不表示每个 SeaDAS 安装或每次 RGB 操作都会发生。遇到类似提示时，应同时核对日志，避免把其他原因套用到本补丁。

典型日志片段如下；不同构建的行号可能不同：

```text
java.lang.NullPointerException:
Cannot invoke "RasterDataNode.getProduct()"
because ProductSceneView.getRaster() is null

ProductSceneView.getProduct
ColorManipulationFormImpl.setProductSceneView
ColorManipulationFormImpl.selectionChange
```

还可能看到这条初始化回调链：

```text
MoreOptionsForm.updateForm
  -> setHistogramMatching
  -> 属性变更回调
  -> MoreOptionsForm.updateModel
  -> ColorManipulationFormImpl.applyChanges
  -> Continuous3BandGraphicalForm.updateFormModel
  -> getProduct() / getRaster() 空指针
```

## 2. 原理：两个代码问题，以及类缓存的影响

### 2.1 已关闭的旧视图仍被用于解除绑定

颜色面板切换图像时，会先解除旧图像视图的监听，再绑定新图像。

原实现直接调用旧视图的 `getProduct()`。RGB 窗口关闭后，其图像对象或波段引用可能已被清空；此时读取旧视图会抛出空指针，后续新视图绑定不能完成。

第一项修复只改变这个旧视图清理调用：先检查视图、图像对象、波段数组及第一个波段是否仍有效；已失效时返回 `null`，让原有的解绑/重新绑定流程继续执行。

没有全局改写 `ProductSceneView.getProduct()`，也没有捕获并忽略所有空指针异常。

### 2.2 恢复控件时过早触发“应用”，把新 RGB 通道写空

RGB 子面板隐藏时，会清空它保存的当前通道引用。下一次打开 RGB，需要先用新图像初始化这些引用。

有问题的顺序是：先恢复直方图控件，再初始化新 RGB 子面板。把控件从上次的 Equalize 恢复为新图像状态时，会触发属性变更，并自动应用当前面板值。此时通道引用尚未初始化，程序可能把三个空引用写入新 RGB，随后多个依赖当前图像的工具报错。

第二项修复把 `installMoreOptions()` 移到 `handleFormShown()` 之后：

```text
原顺序：安装/恢复直方图控件 → 可能触发自动应用 → 初始化 RGB 通道
修复后：初始化 RGB 通道 → 安装/恢复直方图控件 → 自动应用有效通道
```

这项修复改变的是界面初始化顺序，equalize 的计算公式保持原样。

### 2.3 磁盘 JAR 改好了，运行时仍可能加载缓存旧类

本次还直接观察到：JAR 的校验值已经是补丁版，但正在运行的 SeaDAS 中没有补丁辅助方法。刷新 NetBeans 的 `all-resources.dat` 类资源缓存后，应用才真正加载修改后的类。

因此安装和回退必须同时处理正确用户配置目录中的类缓存。本工具只将 `all-resources.dat` 改名备份，让应用重建；保留用户设置、RGB 配置和配色方案。

**只重启、只检查磁盘 SHA256，或只清缓存，都不能替代完整修复和验证。**

## 3. 修改范围及已验证构建

唯一变化的 JAR 条目是：

```text
snap/modules/org-esa-snap-snap-rcp.jar
└── org/esa/snap/rcp/colormanip/ColorManipulationFormImpl.class
```

工具会核对其余条目的名称和解压后内容全部相同。原始 L1B/L2 文件、Rrs、质量控制、科学处理程序、RGB 波段选择及 equalize 算法均不在修改范围内。

本次精确构建的校验值：

| 对象 | SHA256 |
| --- | --- |
| 原始 RCP JAR | `55662fd780ad09e0b7756422c3799379d1d783e5af27d109e24c5a958f45dc2d` |
| 已验证的第二版补丁 JAR | `e8c62196f85e1421d366dc47cc562f5f4916257cd77a6dae67b3ea2fa4bd7213` |

即使都标为 SeaDAS 12.0.0，不同分发构建的文件也可能不同；版本名称相同不等于二进制相同。

## 4. 环境与目录

需要：

- Python 3.9 或更高版本，只用标准库，不需要 `pip install`。
- 完整 JDK 21 或更高版本，包含 `java`、`javac`、`javap`。SeaDAS 自带的 JRE 通常没有编译器。
- SeaDAS 安装中的 ASM 与匹配的 ASM-tree JAR。本机来自 `platform/core/asm-9.6.jar` 和 `asm-tree-9.6.jar`，工具不自动下载依赖。
- 构建、安装和回退时，对相应目录具有正常文件访问权限。安装/回退自动化目前只在 Windows 上验证。

仓库结构：

```text
README.md
patch.py
src/
  PatchColorManager.java          # 旧视图清理保护
  PatchInitializationOrder.java   # 初始化顺序修复
  LoadedClassProbe.java           # 可选：读取运行中的类是否含保护方法
  AttachProbe.java                # 可选：连接本机 SeaDAS Java 进程
tests/
  test_patch.py                   # 人工安装目录中的部署回归测试
```

`build/`、`work/`、生成的 JAR/class、日志及备份均不提交。

## 5. 第一次安装补丁

以下为 PowerShell 示例。先进入你克隆的仓库目录，并按本机实际路径修改变量：

```powershell
Set-Location 'C:\work\seadas-color-manager-fix'

$fixPython = 'D:\anaconda3\envs\myenv\python.exe'
$fixJdk = 'C:\Program Files\Eclipse Adoptium\jdk-25.0.2.10-hotspot'
$seadasHome = 'D:\SeaDAS'
$seadasCache = Join-Path $env:APPDATA '.seadas\var\cache'
$fixPackage = '.\build\seadas-current'
```

### 5.1 先核实安装文件与用户缓存目录

```powershell
& $fixPython .\patch.py status --seadas-home $seadasHome
```

`status` 只报告磁盘文件，不证明运行时加载状态。

缓存目录必须与当次 SeaDAS 日志头的 **Cache Directory** 一致。使用了自定义 `--userdir`、不同用户配置或其他安装目录时，修改 `$seadasCache`；不要根据 About 中的 Resource directory 猜测缓存路径。

如果输出 `known_local_v2_patch: true`，磁盘上已经是本次验证过的第二版。先做第 6 节验证，不需要再次覆盖安装。

### 5.2 从本机未修改的原始模块构建

```powershell
& $fixPython .\patch.py build `
  --seadas-home $seadasHome `
  --jdk-home $fixJdk `
  --output $fixPackage
```

构建不修改 SeaDAS，输出 `patched.jar`、`manifest.json` 和修改前后反汇编。输出目录必须为空，便于区分不同软件构建。

对于上表的已验证原始 JAR，重建产物必须与上表补丁 SHA256 完全一致，否则工具拒绝交付候选包。

如果当前安装已经打过补丁，而你需要重建，可显式指定对应的完整原始备份：

```powershell
& $fixPython .\patch.py build `
  --seadas-home $seadasHome `
  --jdk-home $fixJdk `
  --input-jar 'D:\path-to-your-backup\original.jar' `
  --output '.\build\rebuilt-from-original'
```

### 5.3 正常退出 SeaDAS，再安装

正常保存工作并退出 SeaDAS；工具不会强制结束进程。

```powershell
& $fixPython .\patch.py install `
  --seadas-home $seadasHome `
  --package $fixPackage `
  --cache-dir $seadasCache
```

工具会：

1. 检查 SeaDAS 已退出。
2. 核对当前模块必须匹配这个候选包的**原始** SHA256。
3. 保存并核对原始 JAR 备份。
4. 在目标目录暂存补丁，核对后替换并再次核对 SHA256。
5. 将指定缓存目录的 `all-resources.dat` 改名保存。
6. 输出部署回执及原始备份目录。

备份按原始校验值分目录保存，例如：

```text
D:\SeaDAS\local_fixes\color-manager\55662fd780ad09e0\
  original.jar
  manifest.json
  deployment.json
```

保留工具输出的实际备份路径。如果安装目录受系统保护，需要在具备相应写权限的终端运行；不要通过改变整个安装盘权限解决。

### 5.4 重新启动

```powershell
Start-Process -FilePath (Join-Path $seadasHome 'bin\seadas64.exe')
```

## 6. 安装后的验证

### 6.1 必须进行的界面验证

在原来会出问题的影像上连续操作至少三轮：

```text
合成 RGB → Equalize → 关闭 RGB → 重新合成 RGB → 再调整颜色
```

每次都确认 Color Manager 能识别新图并继续调色。再检查新会话日志是否仍出现相关空指针。

本机最终验证包括三轮真实窗口测试和用户原影像复验；用户已确认恢复正常。人工窗口测试使用明确标注的 32×32 合成梯度，不能当作遥感数据处理精度验证。

### 6.2 可选：核实正在运行的程序是否真的加载补丁

先打开 Color Manager，然后查询 SeaDAS 的实际进程号：

```powershell
Get-Process seadas* | Select-Object Id, ProcessName, Path

# 把 12345 换成上面查到的正确 SeaDAS 进程号
& $fixPython .\patch.py verify-runtime `
  --jdk-home $fixJdk `
  --pid 12345 `
  --output '.\work\runtime-check'
```

探针通过同一用户的 Java Attach 读取已加载类的方法和来源，不重定义类。预期输出：

```text
patched_helper_loaded=true
```

- `false`：运行时仍加载旧类，应正常退出后核对并刷新正确配置目录的类缓存，再启动。
- 未找到类：先打开 Color Manager 后重试。
- Attach 被本机策略禁用：仍可进行 GUI 验证，不要为了运行探针盲目关闭系统安全设置。

**探针仅证明旧视图保护辅助方法已加载；第一版也有这个方法，故不能单独证明第二版初始化顺序正确。仍需检查完整补丁校验和，并完成三轮 GUI 验证。** Java 21 可能打印动态加载诊断代理的提示，它本身不等于补丁失败。

## 7. 升级 SeaDAS 后怎么补回

### 情况 A：升级后不再出现该故障

保留上游版本即可，不需要为了“保持补丁”覆盖它。

### 情况 B：升级后仍有同一症状和日志

1. 记录新版本、实际 RCP JAR 校验值、缓存路径和复现步骤。
2. 使用升级后的原始 JAR 重新构建；给新候选包换一个输出目录。
3. 审查变化并复测后再安装，安装后刷新缓存并做 GUI 验证。

默认情况下，工具会拒绝未知原始 SHA256。下面的参数仅用于生成**未经验证版本的候选包**：

```powershell
& $fixPython .\patch.py build `
  --seadas-home $seadasHome `
  --jdk-home $fixJdk `
  --output '.\build\after-upgrade-candidate' `
  --allow-unverified-version
```

工具仍会核对类名、方法签名、旧视图调用位置、调用数量和初始化顺序；发现结构变化、已打过补丁或初始化顺序已经改变时会停止。结构检查通过不证明新版本语义兼容，也不证明 GUI 已修好。

对照候选包里的 `original_javap.txt` 和 `patched_javap.txt`，确认修改仍对应第 2 节的两个问题，并在保留原始安装及备份的条件下完成新版本验证。安装未知版本候选需要明确标记：

```powershell
# 仅在已经审阅并准备对新版本验证时运行
& $fixPython .\patch.py install `
  --seadas-home $seadasHome `
  --package '.\build\after-upgrade-candidate' `
  --cache-dir $seadasCache `
  --allow-unverified-version
```

如果构建工具报结构不匹配，应重新分析该版本源码/字节码；不要删掉结构检查强行打补丁。部分问题可能已被上游单独修复，需要重新缩小修改范围。

**升级后的原始 JAR 是新补丁的基线。旧版修好的整份 JAR 不应用来替换新版本模块，也不应用旧备份覆盖升级后的安装。**

## 8. 回退

正常退出 SeaDAS，使用安装时输出的实际备份目录：

```powershell
& $fixPython .\patch.py rollback `
  --seadas-home $seadasHome `
  --backup 'D:\SeaDAS\local_fixes\color-manager\55662fd780ad09e0'
```

默认沿用该备份的部署回执记录的缓存路径；如配置目录已变化，可加 `--cache-dir $seadasCache`。

回退核对：当前模块必须恰好是该补丁，原备份必须匹配对应原始 SHA256。恢复原模块后同样改名保存类缓存，再启动应用。如果安装已经被其他版本升级替换，工具会拒绝回退旧模块。

## 9. 验证工具本身

```powershell
& $fixPython .\tests\test_patch.py
```

8 项部署测试使用临时的人工安装目录，覆盖安装/回退、缓存保存、配置保留、版本不匹配、候选版本标记、备份/补丁校验和正在运行的应用保护，不触碰真实 SeaDAS。

这些测试检查部署流程；不能替代 GUI 复现测试。

## 10. 证据与代码参考

本次修复基于本机安装模块的反汇编、运行类探针、真实 GUI 回归及用户原影像复验。第一版只做旧视图清理路径检查时没有覆盖完整 GUI 初始化链，后续真实窗口测试才定位了第二项问题。

可以参考以下上游资料理解相关机制；`master` 是变化的上游代码，不代表与本机安装版逐字一致：

- [SNAP ColorManipulationFormImpl](https://github.com/senbox-org/snap-desktop/blob/master/snap-rcp/src/main/java/org/esa/snap/rcp/colormanip/ColorManipulationFormImpl.java)：图像切换、子面板安装和自动应用。
- [SNAP ProductSceneView](https://github.com/senbox-org/snap-desktop/blob/master/snap-ui/src/main/java/org/esa/snap/ui/product/ProductSceneView.java)：RGB 视图、波段引用及释放流程。
- [SNAP 12 颜色管理帮助](https://step.esa.int/main/wp-content/help/versions/12.0.0/snap/org.esa.snap.snap.help/desktop/ColorManipulationWindow.html)：颜色编辑与 Histogram matching。

## 11. 上传 GitHub

提交本目录中的 README、Python 工具、Java 源码、测试和 `.gitignore` 即可。`.gitignore` 已排除构建产物、JAR/class 和日志。

该目录不包含 SeaDAS 完整发行模块、卫星影像、用户配置、原始日志或诊断堆转储。日后记录新版本兼容性时，建议保存版本、模块 SHA256、补丁源码版本和 GUI 验证结果，避免只写“某个版本可用”。