import type { Dimension } from './client';

/**
 * 从 report 数据中获取维度配置。
 * 优先使用 report.dimensions，fallback 到空数组。
 */
export function useDimensions(report: { dimensions?: Dimension[] } | null | undefined): Dimension[] {
  return report?.dimensions ?? [];
}

/**
 * 从维度列表构建 label 映射。
 */
export function buildDimensionLabels(dimensions: Dimension[]): Record<string, string> {
  const labels: Record<string, string> = {};
  for (const dim of dimensions) {
    labels[dim.key] = dim.label;
  }
  return labels;
}
