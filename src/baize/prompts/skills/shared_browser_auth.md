# Shared Browser 协作认证技能（按需注入）

当目标 Web 应用需要**人工交互式认证**（图形/SMS/TOTP 验证码、扫码登录、WebAuthn、
反机器人 challenge 等），必须切换到本技能描述的 `shared_browser_*` 协作浏览器流程。
不要在验证码/gate 页面上反复重试 `http_request` / 扫描器 —— 首次遇到交互式认证就切换。

## 工具族
- `shared_browser_open` — 在持久化有头浏览器中打开 URL（登录态跨会话保留）。
- `shared_browser_wait_user` — 等待人工完成扫码/验证码/登录，可配置
  `timeout` 与 `success_url_prefix`，检测到成功后自动返回。
- `shared_browser_snapshot` — 保存当前页面截图，确认登录态或页面状态。
- `shared_browser_click` — 点击 CSS 选择器指定元素（按钮、链接等）。
- `shared_browser_fill` — 填写指定输入框的值。
- `shared_browser_evaluate` — 在页面中执行 JS 表达式读取动态数据。
- `shared_browser_status` — 查询浏览器运行状态和当前 URL。
- `shared_browser_close` — 关闭浏览器（登录态保留在磁盘上）。

## 触发场景
1. 图形 / SMS / 邮件 / TOTP 验证码、reCAPTCHA、hCaptcha、滑块/点击类人机验证。
2. 扫码登录（企业 SSO、企业微信/钉钉/GitHub 扫码等）。
3. 硬件密钥 / WebAuthn / 生物识别 / 设备绑定 MFA。
4. 任何 `http_request` 返回 gate/challenge 的反机器人（如 Cloudflare JS challenge）页面。

## 强制流程
1. `shared_browser_open(<login-or-challenge-url>)` —— 打开持久化可见浏览器窗口；
   cookies 与登录态在后续所有调用间保留。
2. 立刻 `shared_browser_wait_user(<清晰的指示>, timeout=240, success_url_prefix=<预期登录后URL>)`。
   阻塞等待操作员完成认证；操作员可点击 "I'm done" 立即放行，或 `success_url_prefix`
   命中后自动放行。
3. 放行后直接复用已认证浏览器：用 `shared_browser_snapshot(...)` 确认处于登录后页面，
   用 `shared_browser_click/fill/evaluate` 在门户内导航与检查，然后在已认证会话上
   继续原有测试/利用流程。登录态已固化，不要再向用户索取凭据。
4. 结束时调用 `shared_browser_close()` 清理（持久化 profile 仍保留 cookies 供后续使用）。

## 关键纪律
- **首次**看到交互式认证即切换，不要循环重试 HTTP 请求/模糊器 —— 避免浪费 token 与时间。
- `wait_user` 是阻塞调用：调用后立即返回等待结果，不要在同一轮继续发请求探测登录态。
- 浏览器操作目标是授权范围内的页面；不要在浏览器里执行越权或破坏性动作。
