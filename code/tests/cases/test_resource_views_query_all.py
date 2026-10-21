""":module: resource.index-search"""
from management_portal import resource_views
from management_portal.resources.contracts import ResourceQuery
from management_portal.resources.query import query_records


class PagedService:
    """A ResourceService stand-in that returns at most 5,000 records a call."""

    def __init__(self, count: int, *, shift_after_first_page: bool = False):
        self.records = [
            {"resource_id": f"paper_{index:012x}", "title": f"Paper {index}", "attributes": {"year": "2026"}}
            for index in range(count)
        ]
        self.shift = shift_after_first_page
        self.calls = []

    def query(self, **kwargs):
        self.calls.append(kwargs)
        offset, limit = kwargs["offset"], kwargs["limit"]
        records = self.records
        if self.shift and offset:
            # A record was indexed between the pages: the last record of the
            # first page shows up again at the start of the second.
            records = [self.records[0], *self.records]
        return {"total": len(records), "offset": offset, "limit": limit, "resources": records[offset:offset + limit]}


def test_paper_library_reads_every_visible_paper_beyond_one_query_page():
    """Paper Library 读取超过单次查询上限的全部可见论文

    资源索引单次查询最多返回 5,000 条；Paper Library 等列表按页读取，超过 5,000 篇时仍返回全部可见论文。

    :module: workflow.paper
    """
    # A single query returns at most 5,000 records; the Library had 4,055.
    service = PagedService(5_003)
    papers = resource_views.paper_records(actor="alice", service=service)
    assert len(papers) == 5_003
    assert [call["offset"] for call in service.calls] == [0, 5_000]
    assert all(call["resource_types"] == ["paper"] and call["actor"] == "alice" for call in service.calls)


def test_paged_reads_keep_each_record_once_when_the_index_moves_between_pages():
    """分页读取资源索引时每条记录只出现一次

    两页之间索引发生变化、记录在第二页重复出现时，列表按资源 ID 去重。
    """
    service = PagedService(5_003, shift_after_first_page=True)
    papers = resource_views.paper_records(actor="alice", service=service)
    ids = [paper["id"] for paper in papers]
    assert len(ids) == len(set(ids)) == 5_003


def test_paper_library_excludes_reviews_with_the_shared_paper_facet():
    """论文库列表排除同样带 paper 分面的评审，匿名用户得到空列表

    :module: resource.service"""
    paper = {"resource_id": "paper_111111111111", "resource_type": "paper",
             "title": "Paper", "summary": "Abstract", "facets": ["paper"],
             "attributes": {"year": "2026"}}
    review = {"resource_id": "review_222222222222", "resource_type": "review",
              "title": "Review", "facets": ["paper", "review"],
              "attributes": {"paper_id": paper["resource_id"]}}

    class Service:
        def query(self, **kwargs):
            return query_records([paper, review], ResourceQuery.from_kwargs(kwargs))

    papers = resource_views.paper_records(actor="alice", service=Service())
    assert papers == [{"id": paper["resource_id"], "title": "Paper",
                       "abstract": "Abstract", "year": "2026"}]
    assert resource_views.paper_records(actor="", service=Service()) == []
