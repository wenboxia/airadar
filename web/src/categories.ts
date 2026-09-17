/** 按 pipeline 导出的类目表排序；表里没有的（旧数据残留）排在后面。空值不算分类。 */
export function orderCategories(present: Set<string>, order?: string[]): string[] {
  const known = (order ?? []).filter((c) => present.has(c))
  const rest = [...present].filter((c) => c && !known.includes(c)).sort()
  return [...known, ...rest]
}
