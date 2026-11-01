from collections import Counter
import json
from pathlib import Path
import re


def main():
    root = Path(__file__).resolve().parents[1]
    sources = sorted(root.glob('*.tex')) + sorted((root / 'chapters').glob('*.tex')) + sorted((root / 'appendices').glob('*.tex')) + sorted((root / 'data').glob('*.tex'))
    text = '\n'.join(path.read_text() for path in sources)
    bibliography = (root / 'references.bib').read_text()
    keys = set(re.findall(r'@\w+\{([^,]+),', bibliography))
    cited = {key.strip() for group in re.findall(r'\\cite\w*(?:\[[^]]*\])*\{([^}]+)\}', text) for key in group.split(',')}
    assert cited <= keys, cited - keys
    labels = re.findall(r'\\label\{([^}]+)\}', text)
    assert all(count == 1 for count in Counter(labels).values())
    references = set(re.findall(r'\\(?:ref|eqref|pageref|autoref)\{([^}]+)\}', text))
    assert references <= set(labels), references - set(labels)
    for include in re.findall(r'\\(?:input|include)\{([^}]+)\}', text):
        assert (root / (include + '.tex')).is_file(), include
    for source in sources:
        content = source.read_text()
        assert content.count(r'\[') == content.count(r'\]'), source
        stack = []
        for match in re.finditer(r'\\(begin|end)\{([^}]+)\}', content):
            direction, environment = match.groups()
            if direction == 'begin':
                stack.append(environment)
            else:
                assert stack and stack.pop() == environment, (source, environment)
        assert not stack, source
    assert len(list((root / 'chapters').glob('*.tex'))) == 10
    assert len(list((root / 'appendices').glob('*.tex'))) == 3
    assert not re.search(r'TODO|FIXME|待补|占位', text)
    result = {'edition': 3, 'status': 'passed', 'chapters': 10, 'appendices': 3, 'chinese_characters': len(re.findall(r'[\u4e00-\u9fff]', text)), 'references': len(cited)}
    for environment in ('theorem', 'proposition', 'lemma', 'corollary', 'proof', 'exercise', 'figure', 'table'):
        result[environment] = len(re.findall(r'\\begin\{' + environment + r'\}', text))
    assert result['exercise'] == 20
    (root / 'validation/manuscript-check.json').write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
