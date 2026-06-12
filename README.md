# MCP Atlassian

![PyPI Version](https://img.shields.io/pypi/v/mcp-atlassian)
![PyPI - Downloads](https://img.shields.io/pypi/dm/mcp-atlassian)
![PePy - Total Downloads](https://static.pepy.tech/personalized-badge/mcp-atlassian?period=total&units=international_system&left_color=grey&right_color=blue&left_text=Total%20Downloads)
[![Run Tests](https://github.com/sooperset/mcp-atlassian/actions/workflows/tests.yml/badge.svg)](https://github.com/sooperset/mcp-atlassian/actions/workflows/tests.yml)
![License](https://img.shields.io/github/license/sooperset/mcp-atlassian)
[![Docs](https://img.shields.io/badge/docs-mintlify-blue)](https://mcp-atlassian.soomiles.com)

Model Context Protocol (MCP) server for Atlassian products (Confluence and Jira). Supports both Cloud and Server/Data Center deployments.

https://github.com/user-attachments/assets/35303504-14c6-4ae4-913b-7c25ea511c3e

<details>
<summary>Confluence Demo</summary>

https://github.com/user-attachments/assets/7fe9c488-ad0c-4876-9b54-120b666bb785

</details>

## 本機修改版連線指南(zh-TW)

> 這是基於 [sooperset/mcp-atlassian](https://github.com/sooperset/mcp-atlassian) 的本機修改版,
> 額外新增 4 個 Jira 圖片/附件工具(上游沒有):
>
> | 工具 | 用途 |
> |------|------|
> | `jira_upload_attachment` | 上傳附件(本機路徑或 base64) |
> | `jira_embed_image_in_description` | 上傳圖片並嵌入描述內文渲染,支援 `append`/`prepend`/`marker` 三種定位(marker 模式可把圖放進表格儲存格) |
> | `jira_add_comment_with_image` | 新增留言並內嵌圖片 |
> | `jira_delete_attachment` | 刪除附件(用 attachment id 或 issue+檔名) |

### A. 第一次連 MCP(從零開始)

**1. 取得 Jira API Token**

到 https://id.atlassian.com/manage-profile/security/api-tokens 按「Create API token」,
複製產生的 token(只會顯示一次)。

**2. 安裝 uv 與專案依賴**

```bash
# 安裝 uv(已安裝可跳過)
curl -LsSf https://astral.sh/uv/install.sh | sh

# 取得本專案並安裝依賴
git clone <本 repo 位址> ~/jira-mcp   # 或放任何路徑
cd ~/jira-mcp
uv sync
```

**3. 設定 Claude Desktop**

編輯設定檔(沒有就新建):

- macOS:`~/Library/Application Support/Claude/claude_desktop_config.json`
- Windows:`%APPDATA%\Claude\claude_desktop_config.json`

```json
{
  "mcpServers": {
    "mcp-atlassian": {
      "command": "uv",
      "args": [
        "--directory",
        "/你的絕對路徑/jira-mcp",
        "run",
        "mcp-atlassian"
      ],
      "env": {
        "JIRA_URL": "https://your-company.atlassian.net",
        "JIRA_USERNAME": "your.email@company.com",
        "JIRA_API_TOKEN": "你的 API token"
      }
    }
  }
}
```

注意事項:

- `--directory` 後面要填本 repo 的**絕對路徑**(不能用 `~`)
- 若 `uv` 不在系統 PATH,`command` 請填完整路徑(用 `which uv` 查,通常是 `~/.local/bin/uv`,要展開成絕對路徑)
- token 一律放在設定檔的 `env` 區,不要寫進任何會 commit 的檔案
- 也要用 Confluence 的話,在 `env` 加上 `CONFLUENCE_URL`(`https://your-company.atlassian.net/wiki`)、`CONFLUENCE_USERNAME`、`CONFLUENCE_API_TOKEN`

**4. 重啟並驗證**

完全結束 Claude Desktop(macOS 按 Cmd+Q,不是只關視窗)再重開。
對 Claude 說「列出我在 Jira 的專案」,有正常回應即連線成功;
說「把某張本機圖片嵌入某張票的描述」可驗證本修改版專屬工具。

### B. 從官方發佈版(uvx / Docker)切換到本修改版

如果你原本已經照官方 README 用 `uvx mcp-atlassian` 或 Docker 跑,
只需要改 `command` / `args` 兩個欄位,`env` 整段保留不動:

```diff
 {
   "mcpServers": {
     "mcp-atlassian": {
-      "command": "uvx",
-      "args": ["mcp-atlassian"],
+      "command": "uv",
+      "args": [
+        "--directory",
+        "/你的絕對路徑/jira-mcp",
+        "run",
+        "mcp-atlassian"
+      ],
       "env": {
         "JIRA_URL": "https://your-company.atlassian.net",
         "JIRA_USERNAME": "your.email@company.com",
         "JIRA_API_TOKEN": "your_api_token"
       }
     }
   }
 }
```

Docker 版同理:把整個 `command`/`args` 換成上面 `uv --directory ... run mcp-atlassian` 的形式即可。

差異說明:

- `uvx mcp-atlassian` 跑的是 **PyPI 上的官方發佈版**,沒有本修改版的 4 個圖片/附件工具
- `uv --directory <路徑> run mcp-atlassian` 跑的是**這個資料夾裡的原始碼**,改完程式碼後重啟 Claude Desktop 就生效,不需要重新安裝
- 想換回官方版,把 `command`/`args` 改回原樣即可,隨時可逆

改完設定後一樣要完全重啟 Claude Desktop 才會生效。

---

## Quick Start

### 1. Get Your API Token

Go to https://id.atlassian.com/manage-profile/security/api-tokens and create a token.

> For Server/Data Center, use a Personal Access Token instead. See [Authentication](https://mcp-atlassian.soomiles.com/docs/authentication).

### 2. Configure Your IDE

Add to your Claude Desktop or Cursor MCP configuration:

```json
{
  "mcpServers": {
    "mcp-atlassian": {
      "command": "uvx",
      "args": ["mcp-atlassian"],
      "env": {
        "JIRA_URL": "https://your-company.atlassian.net",
        "JIRA_USERNAME": "your.email@company.com",
        "JIRA_API_TOKEN": "your_api_token",
        "CONFLUENCE_URL": "https://your-company.atlassian.net/wiki",
        "CONFLUENCE_USERNAME": "your.email@company.com",
        "CONFLUENCE_API_TOKEN": "your_api_token"
      }
    }
  }
}
```

> **Server/Data Center users**: Use `JIRA_PERSONAL_TOKEN` instead of `JIRA_USERNAME` + `JIRA_API_TOKEN`. See [Authentication](https://mcp-atlassian.soomiles.com/docs/authentication) for details.

### 3. Start Using

Ask your AI assistant to:
- **"Find issues assigned to me in PROJ project"**
- **"Search Confluence for onboarding docs"**
- **"Create a bug ticket for the login issue"**
- **"Update the status of PROJ-123 to Done"**

## Documentation

Full documentation is available at **[mcp-atlassian.soomiles.com](https://mcp-atlassian.soomiles.com)**.

Documentation is also available in [llms.txt format](https://llmstxt.org/), which LLMs can consume easily:
- [`llms.txt`](https://mcp-atlassian.soomiles.com/llms.txt) — documentation sitemap
- [`llms-full.txt`](https://mcp-atlassian.soomiles.com/llms-full.txt) — complete documentation

| Topic | Description |
|-------|-------------|
| [Installation](https://mcp-atlassian.soomiles.com/docs/installation) | uvx, Docker, pip, from source |
| [Authentication](https://mcp-atlassian.soomiles.com/docs/authentication) | API tokens, PAT, OAuth 2.0 |
| [Configuration](https://mcp-atlassian.soomiles.com/docs/configuration) | IDE setup, environment variables |
| [HTTP Transport](https://mcp-atlassian.soomiles.com/docs/http-transport) | SSE, streamable-http, multi-user |
| [Tools Reference](https://mcp-atlassian.soomiles.com/docs/tools-reference) | All Jira & Confluence tools |
| [Troubleshooting](https://mcp-atlassian.soomiles.com/docs/troubleshooting) | Common issues & debugging |

## Compatibility

| Product | Deployment | Support |
|---------|------------|---------|
| Confluence | Cloud | Fully supported |
| Confluence | Server/Data Center | Supported (v6.0+) |
| Jira | Cloud | Fully supported |
| Jira | Server/Data Center | Supported (v8.14+) |

## Key Tools

| Jira | Confluence |
|------|------------|
| `jira_search` - Search with JQL | `confluence_search` - Search with CQL |
| `jira_get_issue` - Get issue details | `confluence_get_page` - Get page content |
| `jira_create_issue` - Create issues | `confluence_create_page` - Create pages |
| `jira_update_issue` - Update issues | `confluence_update_page` - Update pages |
| `jira_transition_issue` - Change status | `confluence_add_comment` - Add comments |

**72 tools total** — See [Tools Reference](https://mcp-atlassian.soomiles.com/docs/tools-reference) for the complete list.

## Security

Never share API tokens. Keep `.env` files secure. See [SECURITY.md](SECURITY.md).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) for development setup.

## License

MIT - See [LICENSE](LICENSE). Not an official Atlassian product.
