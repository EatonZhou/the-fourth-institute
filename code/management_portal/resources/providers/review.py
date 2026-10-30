"""Paper owns Review deletion, including its bound work and stored document."""
from .common import canonical_record


def install_mutation_provider(service):
    from management_portal import paper_reviews, paper_state_store

    def unregister(resource_id, *, actor="", **_context):
        if any(job.get("review_id") == resource_id and job.get("status") in {"queued", "running"}
               for job in paper_state_store.records("review_jobs")):
            raise RuntimeError(f"{resource_id}: 评审生成尚未结束")
        if paper_state_store.get("reviews", resource_id) is not None:
            try:
                paper_reviews.delete_review({"review_id": resource_id}, actor)
            except KeyError:
                if paper_state_store.get("reviews", resource_id) is not None:
                    raise
        if paper_state_store.get("reviews", resource_id) is not None:
            raise RuntimeError(f"{resource_id}: 删除尚未完成")
        return canonical_record(resource_id, {"deleted": True})

    service.register_mutation_provider("review", unregister=unregister)
