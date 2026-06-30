# MCP Browser Connect Flow

This is the first server-side browser/device flow for GitHub-based MCP
installation.

The goal is to let an installer ask the user to approve access in a browser
instead of pasting a license key into a terminal.

## Flow

1. Installer starts a connection request.

```http
POST /v1/mcp/connect/start
Content-Type: application/json

{
  "product_id": "law",
  "client_name": "claude-desktop"
}
```

Response:

```json
{
  "device_code": "installer-private-code",
  "user_code": "ABCD-2345",
  "verification_url": "https://lawtasksai.com/connect?code=ABCD-2345&product=law",
  "expires_in": 600,
  "interval": 2
}
```

2. Website approves the code after the user signs in or enters a license key.

```http
POST /v1/mcp/connect/approve
Content-Type: application/json

{
  "user_code": "ABCD-2345",
  "license_key": "lt_..."
}
```

3. Installer polls for the credential.

```http
POST /v1/mcp/connect/token
Content-Type: application/json

{
  "device_code": "installer-private-code"
}
```

While waiting, the API returns HTTP 428 with `authorization_pending`.

After approval, the API returns:

```json
{
  "access_token": "lt_...",
  "token_type": "bearer",
  "license_key": "lt_...",
  "product_id": "law",
  "credits_remaining": 50
}
```

## MVP Scope

This MVP returns the existing license key because current MCP runtime endpoints
authenticate with `Authorization: Bearer {license_key}`.

A later version should replace that with scoped, revocable MCP tokens while
keeping the same browser approval shape.

## Privacy

These endpoints do not accept user prompts, documents, task content, workflow
answers, or generated work product. They only handle installer connection
metadata and license approval.
