'use strict';

// Pure matching for this case's one plain-text, unquoted native Chat input.
// Use on the original request from the actual B Page, registered before Send.
// This does not submit, wait, read a response, hide a panel, or establish ready.
function matchesMailboxTextSubmission(args, { residentId, text } = {}) {
  if (!/^resident_[a-f0-9]{12}$/.test(residentId || '') || typeof text !== 'string' || !text.trim()) {
    throw new TypeError('Expected the exact owned Resident and original nonempty text');
  }
  if (!args || args.resident_id !== residentId || args.user_id != null
      || (args.activation_policy != null && args.activation_policy !== 'enqueue_and_trigger')) return false;
  const message = args.message;
  if (!message || typeof message !== 'object' || Array.isArray(message)
      || (message.role != null && message.role !== 'user') || message.quote != null) return false;
  const parts = message.content_parts;
  return Array.isArray(parts) && parts.length === 1 && parts[0]?.type === 'text'
    && typeof parts[0].text === 'string' && parts[0].text === text;
}

module.exports = { matchesMailboxTextSubmission };
