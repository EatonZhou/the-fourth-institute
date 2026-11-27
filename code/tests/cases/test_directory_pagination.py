"""Directory keyset pages have bounded candidate memory and no total ceiling."""
import pytest

from management_portal.resources import directory_pagination as pages


def key(name):
    return (0 if name.endswith('/') else 1, name.casefold(), name)


def test_all_entries_and_casefold_ties():
    names = [f'f{i:05}' for i in range(1531)] + ['Aa', 'aa', 'aA', 'dir/']
    cursor = None
    seen = []
    while True:
        page = pages.paginate(iter(reversed(names)), scope='repo_a/file', cursor=cursor, limit=137, key=key)
        seen.extend(page['entries'])
        cursor = page['next_cursor']
        if not cursor:
            break
    assert seen == sorted(names, key=key)


def test_mutations_before_cursor_do_not_shift_pages():
    first = pages.paginate(iter(['b', 'd', 'f', 'h']), scope='dir', limit=2, key=key)
    # Delete an already returned item, insert on either side of the boundary.
    second = pages.paginate(iter(['a', 'd', 'e', 'f', 'h']), scope='dir', cursor=first['next_cursor'], limit=20, key=key)
    assert first['entries'] == ['b', 'd']
    assert second['entries'] == ['e', 'f', 'h']
    assert not second['next_cursor']


@pytest.mark.parametrize('cursor', ['garbage', 'e30=', 123])
def test_invalid_cursor(cursor):
    with pytest.raises(ValueError, match='cursor'):
        pages.paginate(iter([]), scope='dir', cursor=cursor, key=key)


def test_cursor_cannot_cross_directory_or_binding():
    first = pages.paginate(iter(['a', 'b']), scope='dir:inode1', limit=1, key=key)
    with pytest.raises(ValueError, match='cursor'):
        pages.paginate(iter(['a', 'b']), scope='dir:inode2', cursor=first['next_cursor'], key=key)


@pytest.mark.parametrize('limit', [0, -1, True, '10', 1.5])
def test_invalid_limit(limit):
    with pytest.raises(ValueError, match='limit'):
        pages.paginate(iter([]), scope='dir', limit=limit, key=key)


def test_memory_retains_only_page_candidates():
    class Row:
        live = peak = 0
        def __init__(self, n):
            self.n = n
            Row.live += 1
            Row.peak = max(Row.peak, Row.live)
        def __del__(self):
            Row.live -= 1
    page = pages.paginate((Row(n) for n in reversed(range(10000))), scope='dir', limit=17,
                          key=lambda row: (1, str(row.n).zfill(5), str(row.n)))
    assert [row.n for row in page['entries']] == list(range(17))
    assert Row.peak <= 40
