import review from './exploration-source-review.js';
import { contentDigest } from '../shared/content-digest.js';
import { MAP_REF } from './model.js';

// The matrix applies only to the document its author actually reviewed. A new
// document remains readable; until reviewed it cannot claim current identities.
export function reviewedSources(modelText) {
  return contentDigest(modelText) === review.model.sha256 ? review : null;
}

export function currentObjectReview(bundle, target) {
  if (bundle.mode !== 'current' || !target || target.identity.local_only) return null;
  const item = bundle.sourceReview?.objects[target.id];
  if (item?.status !== 'current' || item.address !== MAP_REF
    || target.identity.resource_ref !== item.address || target.identity.focus_kind !== item.kind
    || target.identity.focus_owner_id !== item.owner) return null;
  return item;
}

export function currentFeedbackEligible(bundle, target) {
  return !!currentObjectReview(bundle, target);
}

export function evidenceStatus(bundle, evidence) {
  const item = bundle.sourceReview?.evidence[evidence.id];
  return item?.address === evidence.address ? item.status : 'unconfirmed';
}
