"""Persist the workflow's complete recovery handle inside a generation receipt.

Generation progress owns its top-level fields. Submission identity and evidence
belong to WorkflowBindings; keep that contract intact instead of flattening it.
"""
from management_portal.lineage_workflows import WorkflowBindings, WorkflowFailed, WorkflowPending


def prepare(job):
    """Prepare the locator; the caller must persist this job before dispatch."""
    job['receipt'] = WorkflowBindings.prepare(job, request_id=job['id'])


def advance(job, *, actor, submit=False):
    """Only the initial caller submits; all recovery observes the original key."""
    if not job.get('receipt'):
        # Pre-handle submitting jobs used this same stable request identity.
        # Reconstruct its locator, not an assertion that it was never sent.
        prepare(job)
    handle = job['receipt']
    try:
        workflow = WorkflowBindings()
        if submit:
            handle = workflow.trigger(handle, actor=actor, prompt=job['request_prompt'], request_id=job['id'],
                                      temporary_credentials=[{"a": "@contributor", "resource_id": job['workbench_id']}])
        else:
            handle = workflow.observe(handle, actor=actor)
    except WorkflowPending as error:
        handle = error.handle
        job.update(status='submitting', error=str(error))
    except WorkflowFailed as error:
        handle = error.handle
        job.update(status='failed', error=str(error))
    except Exception as error:
        # An interrupted dispatch or an unavailable observation is not proof
        # of rejection. Retain the prepared locator and let later reads resolve it.
        job.update(status='submitting', error=str(error))
    else:
        job['status'] = 'running'
        job.pop('error', None)
    job['receipt'] = dict(handle)
    # These are the existing generation read model, not submission's namespace.
    for key in ('message_id', 'event_id', 'trigger_path_id'):
        if handle.get(key):
            job[key] = handle[key]
