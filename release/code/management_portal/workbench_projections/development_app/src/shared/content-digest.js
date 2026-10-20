import { sha256 } from '@noble/hashes/sha2.js';
import { bytesToHex } from '@noble/hashes/utils.js';

// LAN HTTP and sandboxed projections do not necessarily expose WebCrypto.
export function contentDigest(content) {
  const bytes = typeof content === 'string' ? new TextEncoder().encode(content) : content;
  return bytesToHex(sha256(bytes));
}
