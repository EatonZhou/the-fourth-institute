// Local date/module filters do not change the underlying reports.
export const itemKey = (ref, module, id) => JSON.stringify([ref, module, id]);
export const browsingModule = (row) =>
  row.module_attribution || {
    id: row.module_id,
    title: row.module_title,
  };
// A recommendation in a later report keeps the original achievement date.
// Use the same calendar zone as the stage headings, independently of the browser zone.
const calendar = new Intl.DateTimeFormat("en-CA", {
  timeZone: "Asia/Hong_Kong", year: "numeric", month: "2-digit", day: "2-digit",
});
export function achievementDate(row) {
  const time = Date.parse(row.report?.end_at);
  if (!Number.isFinite(time)) return "";
  const parts = Object.fromEntries(calendar.formatToParts(time).map(p => [p.type, p.value]));
  return `${parts.year}-${parts.month}-${parts.day}`;
}
export function dateRangeError({ start_date = "", end_date = "" } = {}) {
  for (const value of [start_date, end_date]) {
    if (!value) continue;
    const time = Date.parse(`${value}T00:00:00Z`);
    if (!/^\d{4}-\d{2}-\d{2}$/.test(value) || !Number.isFinite(time) ||
        new Date(time).toISOString().slice(0, 10) !== value)
      return "请选择有效的起止日期。";
  }
  return start_date && end_date && start_date > end_date
    ? "起始日期不能晚于结束日期。" : "";
}
export function filterItems(items, filter = {}) {
  if (dateRangeError(filter)) return [];
  const query = (filter.query || "").trim().toLocaleLowerCase();
  return items.filter(row => {
    const date = achievementDate(row);
    return (!filter.start_date || (date && date >= filter.start_date)) &&
      (!filter.end_date || (date && date <= filter.end_date)) &&
      (!filter.module_filter || (filter.module_ids || [filter.module_filter]).includes(browsingModule(row).id)) &&
      (!query || `${row.title} ${row.effect || ""} ${browsingModule(row).title || ""}`.toLocaleLowerCase().includes(query));
  });
}
export function moduleScope(modules, id) {
  const ids = new Set([id]);
  let added = true;
  while (added) {
    added = false;
    for (const m of modules)
      if (ids.has(m.parent) && !ids.has(m.id)) {
        ids.add(m.id);
        added = true;
      }
  }
  return [...ids];
}
