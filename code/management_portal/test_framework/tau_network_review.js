'use strict';
const fs = require('node:fs');

// Evidence joins only: a known cancellation owner is not a business verdict.
// Never correlate by URL, action window, elapsed time or neighbouring events.
function reviewRequests(rows) {
  const starts = new Map(rows.filter(row => row.kind === 'request_started').map(row => [row.request_id, row]));
  const proofs = rows.filter(row => ['portal_connection_closed', 'portal_request_aborted', 'pdf_reader_cancelled'].includes(row.kind)
    && row.request_id && row.document_id && row.frame_id && row.request_association !== 'unavailable_no_unique_transport_identity');
  return rows.filter(row => row.kind === 'request_failed').map(end => ({...starts.get(end.request_id), ...end})).map(row => ({
    request_id: row.request_id, frame_id: row.frame_id, document_id: row.document_id,
    path: row.path, failed_reason: row.failed_reason,
    failure_kind: row.failed_reason === 'net::ERR_ABORTED' ? 'aborted'
      : row.failed_reason === 'net::ERR_CONNECTION_REFUSED' ? 'connection_refused'
      : row.failed_reason ? 'other_network_failure' : 'unknown',
    proofs: proofs.filter(proof => proof.request_id === row.request_id
      && proof.frame_id === row.frame_id && proof.document_id === row.document_id),
  }));
}

function writeNetworkReview(input, output) {
  const rows = fs.readFileSync(input, 'utf8').split('\n').filter(Boolean).map(JSON.parse);
  const requests = reviewRequests(rows);
  const result = { requests, failed_count: requests.length,
    // Missing causal observations remain unknown; they are report material.
    unknown_count: requests.filter(row => !row.proofs.length).length,
    unproven_abort_count: requests.filter(row => row.failure_kind === 'aborted' && !row.proofs.length).length,
    connection_refused_count: requests.filter(row => row.failure_kind === 'connection_refused').length };
  fs.writeFileSync(output, JSON.stringify(result, null, 2));
  return result;
}
module.exports = { reviewRequests, writeNetworkReview };
