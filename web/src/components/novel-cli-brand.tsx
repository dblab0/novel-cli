import { novelCliVersion } from "@/lib/version";
import { cn } from "@/lib/utils";

type NovelCliBrandProps = {
  className?: string;
  size?: "sm" | "md";
  showVersion?: boolean;
};

export function NovelCliBrand({
  className,
  size = "md",
  showVersion = true,
}: NovelCliBrandProps) {
  const textSizeClass = size === "sm" ? "text-base" : "text-lg";
  const versionPadding = size === "sm" ? "text-xs" : "text-sm";
  const logoSize = size === "sm" ? "size-6" : "size-7";
  const logoPx = size === "sm" ? 24 : 28;

  return (
    <div className={cn("flex items-center gap-2", className)}>
      <div className="flex items-center gap-2">
        <img
          src="/logo.png"
          alt="Novel"
          width={logoPx}
          height={logoPx}
          className={logoSize}
        />
        <span className={cn(textSizeClass, "font-semibold text-foreground")}>
          Novel Agent
        </span>
      </div>
      {showVersion && (
        <span
          className={cn("text-muted-foreground font-medium", versionPadding)}
        >
          v{novelCliVersion}
        </span>
      )}
    </div>
  );
}