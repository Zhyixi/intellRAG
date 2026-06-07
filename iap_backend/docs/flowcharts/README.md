# ES Retrieve 流程图

本目录包含 `es_retrive` 混合检索策略的可下载流程图。

## 文件说明

| 文件 | 说明 |
|------|------|
| `es_retrieve_overview.mmd` | Mermaid 源码：整体 API 调用链 |
| `es_retrieve_internal.mmd` | Mermaid 源码：单 index 内部策略 |
| `es_retrieve_overview.svg` | 整体流程图（矢量，推荐） |
| `es_retrieve_overview.png` | 整体流程图（位图） |
| `es_retrieve_internal.svg` | 内部策略流程图（矢量，推荐） |
| `es_retrieve_internal.png` | 内部策略流程图（位图） |
| `es_retrieve_flowcharts.zip` | 以上文件打包 |

## 下载方式

在 Cursor / VS Code 中：

1. 打开左侧文件树 `docs/flowcharts/`
2. 右键目标文件 → **Download...**

或在终端复制到本地：

```bash
# 示例：复制整个目录
scp -r user@host:/app/docs/flowcharts ./flowcharts
```

## 重新生成

若修改了 `.mmd` 源码，可用 [Mermaid Live Editor](https://mermaid.live) 粘贴后导出 PNG/SVG。
