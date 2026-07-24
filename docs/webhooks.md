# Signed job webhooks

Create or rotate your signing secret:

```bash
curl -X POST http://localhost:8000/v1/webhooks/secret \
  -H "Authorization: Bearer $CLEANPDF_API_KEY"
```

The raw secret is returned once. Store it securely. A completed or failed async
job with `webhook_url` sends JSON with:

```text
X-CleanPDF-Signature: t=<unix timestamp>,v1=<hex HMAC-SHA256>
```

The signed bytes are exactly `<timestamp>.<raw request body>`. Reject signatures
older or newer than five minutes.

## Python verification

```python
import hashlib
import hmac
import time


def verify(secret: str, body: bytes, header: str) -> bool:
    parts = dict(part.split("=", 1) for part in header.split(","))
    timestamp = int(parts["t"])
    if abs(int(time.time()) - timestamp) > 300:
        return False
    signed = str(timestamp).encode() + b"." + body
    expected = hmac.new(secret.encode(), signed, hashlib.sha256).hexdigest()
    return hmac.compare_digest(expected, parts["v1"])
```

## Node verification

```javascript
import crypto from "node:crypto";

export function verify(secret, bodyBuffer, header) {
  const parts = Object.fromEntries(
    header.split(",").map((part) => part.split("=", 2))
  );
  const timestamp = Number(parts.t);
  if (!Number.isFinite(timestamp) ||
      Math.abs(Math.floor(Date.now() / 1000) - timestamp) > 300) {
    return false;
  }
  const expected = crypto
    .createHmac("sha256", secret)
    .update(Buffer.concat([Buffer.from(`${timestamp}.`), bodyBuffer]))
    .digest("hex");
  const supplied = Buffer.from(parts.v1 || "", "hex");
  const wanted = Buffer.from(expected, "hex");
  return supplied.length === wanted.length &&
    crypto.timingSafeEqual(supplied, wanted);
}
```

The service validates callback targets, signs the raw body, retries three
times with backoff, and marks successful delivery in the job record.
