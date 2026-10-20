import {renderRpc,renderIdentity,renderPipeline} from './lineage-mechanisms.protocols.js';
import {renderPacket,renderOccurrence,renderScript} from './lineage-mechanisms.automation.js';
import {renderDefinition,renderCompile,renderComposition,renderTarget} from './lineage-mechanisms.definition.js';
import {renderIntake,renderBinding,renderRound} from './lineage-mechanisms.conversation.js';
import {renderFamilyRouting,renderFamilySupervision} from './lineage-mechanisms.family-routing.js';

// Explicit identity dispatch, never inference from a shared adapter or array order.
export const lineageDomainViews=Object.freeze({
  'mechanism.lineage.rpc-dispatch':renderRpc,
  'mechanism.lineage.operation-identity':renderIdentity,
  'mechanism.lineage.event-effect-pipeline':renderPipeline,
  'mechanism.lineage.packet-operators':renderPacket,
  'mechanism.lineage.event-occurrence':renderOccurrence,
  'mechanism.lineage.script-runs':renderScript,
  'mechanism.lineage.definition-commit':renderDefinition,
  'mechanism.lineage.compile':renderCompile,
  'mechanism.lineage.composition':renderComposition,
  'mechanism.lineage.application-target':renderTarget,
  'mechanism.lineage.intake':renderIntake,
  'mechanism.lineage.execution-binding':renderBinding,
  'mechanism.lineage.conversation-round':renderRound,
  'mechanism.lineage.family-routing':renderFamilyRouting,
  'mechanism.lineage.family-supervision':renderFamilySupervision,
});
