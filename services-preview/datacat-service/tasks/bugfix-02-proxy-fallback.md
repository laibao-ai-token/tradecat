# Bugfix-02：符号兜底代理一致化

## 目标

- DATACAT_HTTP_PROXY 可影响 REST 兜底符号加载。

## 变更点

- 统一在 REST 兜底调用前注入 HTTP_PROXY/HTTPS_PROXY。

## 验收

- 仅设置 DATACAT_HTTP_PROXY 时，REST 兜底走代理。
