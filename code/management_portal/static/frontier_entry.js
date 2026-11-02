const p=new URLSearchParams(location.search);
location.replace(p.get('lineage') ? '/lineage?view=frontier&lineage='+encodeURIComponent(p.get('lineage')) : p.get('project') ? '/projects?view=map&frontier=1&project='+encodeURIComponent(p.get('project')) : '/projects');
