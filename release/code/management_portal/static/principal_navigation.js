(() => {
  "use strict";

  // projectFilter keeps one Project ("all" keeps every Project); nonEmptyOnly hides
  // Projects that have neither a visible child Lineage nor a Project-level Lineage.
  function projectGroups(hierarchy, residents, {
    query = "", runningOnly = false, isRunning = () => false, projectFilter = "all", nonEmptyOnly = false,
  } = {}) {
    const byId = new Map(residents.map(item => [item.resident_id, item]));
    const byLineage = new Map(residents.map(item => [item.lineage_id, item]));
    const search = query.trim().toLocaleLowerCase();
    // An exact ID finds the Project or Lineage as well as its title does.
    const matches = (...values) => values.some(value => String(value || "").toLocaleLowerCase().includes(search));
    return (hierarchy?.projects || []).filter(project => projectFilter === "all" || project.project_id === projectFilter).map(project => {
      const children = project.lineages.map(child => {
        const lineage = byLineage.get(child.lineage_id) || byId.get(child.resident_id) || {};
        const leaderId = lineage.lineage_leader_resident_id
          || lineage.lineage?.leader_resident_id || child.resident_id || "";
        return {
          ...lineage,
          resident_id: leaderId, lineage_id: child.lineage_id,
          lineage: { ...lineage.lineage, lineage_id: child.lineage_id, leader_resident_id: leaderId },
          lineage_title: child.title, title: child.title, project_id: project.project_id,
        };
      });
      const projectMatches = matches(project.title, project.project_id);
      return {
        id: `lineage:${project.project_id}`, ...project, itemType: "resident",
        items: children.filter(child => (!search || projectMatches || matches(child.title, child.lineage_id)) && (!runningOnly || isRunning(child))),
      };
    }).filter(project => (!search || project.items.length || matches(project.title, project.project_id))
      && (!runningOnly || project.items.length)
      && (!nonEmptyOnly || project.items.length || !!project.lineage_id));
  }

  window.PortalPrincipalNavigation = Object.freeze({ projectGroups });

})();
