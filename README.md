# 佳明体能健康分析

在你自己的电脑上打开一个页面，用 **Garmin Connect Personal API** 读取你自己的活动与健康数据，计算训练负荷和恢复指标，并画出图。还没有配置密钥或尚未同步时，页面用一套标明为**示例数据**的演示序列，方便先看到图表。

认证方式：在 Garmin Connect 网页创建 Personal API Key，本工具用该 Key 换取访问令牌（Bearer），再调用 Personal / connectapi 资源拉取数据。**不再使用邮箱/密码或 MFA 登录。**

官方说明：<https://developer.garmin.com/gc-developer-program/personal/personal-api/>

## 运行

需要 Python 3.11 或更高版本。在本目录：

```bash
bash run.sh
```

脚本有执行权限时也可以 `chmod +x run.sh && ./run.sh`。

浏览器打开：<http://127.0.0.1:8765>

`run.sh` 会在没有 `.venv` 时创建虚拟环境，缺依赖时执行 `pip install -r requirements.txt`，然后只监听 `127.0.0.1`。停掉服务用 Ctrl+C。

也可以手动做同样的事：

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m uvicorn app.main:app --host 127.0.0.1 --port 8765
```

入口文件是 `app/main.py`。

Windows 可以双击或在命令行运行 `run.bat`（同样创建 `.venv`、安装依赖、打开 <http://127.0.0.1:8765>）。

## 第一次使用

1. 登录 [Garmin Connect 网页](https://connect.garmin.com/)，打开 [Personal API Key 页面](https://connect.garmin.com/modern/settings/personalApiToken)（也可在设置里搜索 Personal API）。
2. 选择 **Create Key**，阅读并接受条款，选择过期时间后创建。密钥**只会显示一次**，请立刻复制并妥善保存。
3. 打开本机页面，在「配置」里填写配置名称，粘贴 Personal API Key；选择 **国际区或中国区**（必须与创建 Key 的 Connect 站点一致：`.com` / `.cn`）；可选填写备注标签、性别、年龄、最大心率。
4. 点「保存配置并设为当前」，再点「从佳明同步最近 90 天」。
5. DeepSeek API Key 可填可不填，在同一侧卡片里保存。

可添加多个命名配置，每个配置各自保存一份 Personal API Key。中国区 Key 只能打 `personal.connectapi.garmin.cn`，国际区 Key 只能打 `.com`，不要混用。这些都写在本机，不必改代码。`.env.example` 里没有必填的佳明密钥。可选的 `DEEPSEEK_API_KEY`、`DATA_DIR`、`PORT` 只在你想用环境变量时才需要；复制成 `.env` 即可。只要 `data/secrets.json` 已经存在，DeepSeek 密钥以这个文件为准。

同步窗口是最近 90 天。若接口正常但窗口内无活动/健康数据，页面会提示账号上更早的活动日期（若有），请确认手表已同步到**创建该 Key 的同一个 Connect 账号**。

不要把 Personal API Key 放进命令行、截图或公开仓库。

换取令牌的官方接口：

```bash
curl -X POST 'https://personal.connectapi.garmin.com/personalapitoken-service/personal-api/personal-api-key/token' \
  -H 'Authorization: ApiKey [your-api-key]'
```

访问令牌最长约 24 小时（或随 Key 过期），没有 refresh token；过期后本工具会用同一把 Key 重新换取。

## 密钥放在哪

默认目录是程序旁边的 `data/`（可用 `DATA_DIR` 改掉）：

| 路径 | 内容 |
| --- | --- |
| `data/secrets.json` | 每个配置的 Personal API Key、可选的年龄和心率，以及 DeepSeek 密钥 |
| `data/tokens/<配置>/bearer.json` | 由 API Key 换得的访问令牌缓存 |
| `data/cache/<配置>/bundle.json` | 同步下来并整理过的活动与每日数据 |

密钥文件按 `0600` 写入（只允许当前系统用户读写），目录按 `0700`。`.gitignore` 已忽略 `data/`、`.env` 和 `.venv/`。

换一台电脑：复制整个项目目录。想连同已同步的数据和本机密钥一起搬走，就带上 `data/`，并把它当成私密文件保管。令牌缓存失效时，只要 Key 仍有效，再同步一次即可；Key 过期或丢失时，在 Connect 网页新建 Key 并在页面重新保存。

## DeepSeek 会收到什么

只有同时满足下面三点才会请求 `https://api.deepseek.com` 的 `deepseek-chat`：

- 正在看的是已同步的真实数据，不是示例数据
- 本机保存了 API Key（或还没有 `secrets.json` 时，环境变量里有 `DEEPSEEK_API_KEY`）
- 浏览器打开了页面（或调用了 `/api/coach`）

发出去的是已经算好的指标、警报和本地建议，**不含** Personal API Key、访问令牌，也不含原始活动明细。密钥只放在 `Authorization` 头里。

没填密钥，或请求失败时，页面仍显示本地算好的报告，并写明模型建议不可用。

## 报告里有什么

计算写在 `app/analysis.py`，阈值旁边有简短注释。

- **CTL / ATL / TSB**：42 天和 7 天的指数平均（TrainingPeaks / Coggan）。某一天的 TSB = 前一天的 CTL − 前一天的 ATL。粗读：高于 +25 很轻松，+5 到 +25 较轻松，-10 到 +5 中性，-30 到 -10 累，低于 -30 高疲劳。
- **ACWR**：近 7 日平均日负荷 / 近 28 日平均日负荷。常被引用的区间大约是 0.8–1.3；本工具把高于 1.5 标为尖峰，低于 0.8 标为偏低。这是负荷跳变的提示，不是受伤概率。
- **负荷口径**：优先用佳明活动上的 `activityTrainingLoad`（不是 TrainingPeaks 的 TSS）。否则用 Banister TRIMP（分男女系数），性别未填时用 Edwards 分区 TRIMP，再没有心率时用「分钟 × 5」占位。
- **静息心率**：近 7 日均值相对再往前最多 28 日的均值，升高达到 5 bpm 才标记。
- **HRV**：近 3 次夜间值相对再往前最多 28 次的中位数，低于 90% 才标记。
- **睡眠**：近 7 日平均短于 7 小时，或最近一夜短于 6 小时。
- **恢复不足**：上面睡眠 / 静息心率 / HRV 至少一项偏离，并且 TSB 低于 -10 或 ACWR 高于 1.3。
- **近 7 天与再往前 7 天的时长、距离**，以及按周堆叠的运动时长。
- **强度分布**：有心率时，低于 80% 最大心率算低，达到或高于 90% 算高。这是整段课用一个平均心率，用来对照常说的极化 80/20，不是实验室阈值。
- **设备侧**：能读到时显示 VO2max、训练状态、周训练负荷和佳明自己的负荷区间，以及 Body Battery、压力、步数。

CTL/ATL 从这段数据的第一天以 0 起步，前几周会偏低。页面底部可以展开公式说明。

## 示例数据

还没有成功同步时，图表和数字来自内置的 70 天演示序列，页面上会标明**示例数据**。最后一周被故意加量，并放了偏高的静息心率和偏短的睡眠，用来把警报画出来。那不是你的身体。

## 图表和网络

页面引用 `/static/plotly.min.js`，由已经安装的 `plotly` 包在本机提供，打开页面不必再访问 CDN。

需要网络的时候：第一次 `pip install`、点「同步」连接佳明 Personal API、以及你配置了 DeepSeek 密钥后又在看真实数据时调用 DeepSeek。

服务只绑定 `127.0.0.1`，页面本身没有另一套登录。不要把它改成对局域网开放。

## 测试

```bash
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/python -m pytest
```

测试使用夹具和示例数据，并设置 `GARMIN_FITNESS_DISABLE_NETWORK=1`，不会连接佳明，也不会请求 DeepSeek。覆盖 ACWR、CTL/ATL/TSB、恢复警报，以及带 mock 令牌与 JSON fixture 的 Personal API 同步路径。

## Android 应用（Jetpack Compose）

源码在 `android/`。功能与桌面版对齐：底部三个页签「数据 / 教练 / 设置」；本机保存 Personal API Key 与 DeepSeek Key；国际区 / 中国区端点；同步频率限制与增量缓存；时间范围今天 / 7 / 30 / 90；CTL/ATL/TSB/ACWR 等分析指标与图表；DeepSeek 教练（压缩摘要 + 个人画像时间线）。

### 环境

- JDK 17+
- Android SDK（`compileSdk` / `targetSdk` 34，`minSdk` 26）
- 在 `android/local.properties` 写明 SDK 路径，例如：

```properties
sdk.dir=/path/to/Android/sdk
```

### 编译 Debug APK

```bash
cd android
./gradlew assembleDebug
```

APK 输出：

`android/app/build/outputs/apk/debug/app-debug.apk`

### 安装到手机

```bash
adb install -r android/app/build/outputs/apk/debug/app-debug.apk
```

没有连接设备时，`adb devices` 为空，可先把 APK 拷到手机手动安装。

### 第一次使用（App）

1. 打开「设置」，填写配置名称，粘贴 Garmin Personal API Key，选择国际区或中国区。
2. 可选填写 DeepSeek API Key（教练对话需要）。
3. 回到「数据」，点「从佳明同步」。
4. 在「教练」里基于本机摘要提问。

密钥只保存在 App 私有目录，不会写进仓库。
