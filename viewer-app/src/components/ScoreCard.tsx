/** 分数卡片组件 - 展示单个评估指标分数 */

interface ScoreCardProps {
  /** 标题 */
  title: string;
  /** 分数值 */
  value: number | string;
  /** 副标题或描述 */
  subtitle?: string;
  /** 数值颜色样式类 */
  valueClassName?: string;
}

export function ScoreCard({ title, value, subtitle, valueClassName }: ScoreCardProps) {
  return (
    <div className="rounded-lg border bg-card p-4">
      <div className="text-sm text-muted-foreground mb-1">{title}</div>
      <div className={`text-2xl font-bold ${valueClassName ?? ""}`}>{value}</div>
      {subtitle && (
        <div className="text-xs text-muted-foreground mt-1">{subtitle}</div>
      )}
    </div>
  );
}
