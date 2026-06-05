import { useState } from "react";
import { cn } from "@/lib/utils";
import { BookOpenIcon, XIcon } from "lucide-react";

export type BookSelectorProps = {
  books: string[];
  currentBook: string | null;
  disabled?: boolean;
  onSelect: (bookName: string) => void;
};

export function BookSelector({
  books,
  currentBook,
  disabled = false,
  onSelect,
}: BookSelectorProps) {
  const [hasSelected, setHasSelected] = useState(false);

  const handleSelect = (name: string) => {
    if (disabled || hasSelected) return;
    if (name === currentBook) return;
    setHasSelected(true);
    onSelect(name);
  };

  const handleClear = () => {
    if (disabled || hasSelected) return;
    if (currentBook === null) return;
    setHasSelected(true);
    onSelect("none");
  };

  if (books.length === 0) {
    return (
      <div className="rounded-lg border bg-muted/30 px-4 py-3">
        <div className="flex items-center gap-2 text-sm text-muted-foreground">
          <BookOpenIcon className="size-4 shrink-0" />
          <span>小说知识库未配置或暂无书籍</span>
        </div>
      </div>
    );
  }

  return (
    <div className="rounded-lg border bg-muted/30 overflow-hidden">
      <div className="flex items-center gap-2 px-4 py-2.5 border-b bg-muted/50">
        <BookOpenIcon className="size-4 shrink-0 text-primary" />
        <span className="text-sm font-medium">选择书籍</span>
      </div>
      <div className="divide-y">
        {books.map((name) => {
          const isCurrent = name === currentBook;
          return (
            <button
              key={name}
              type="button"
              disabled={disabled || hasSelected}
              onClick={() => handleSelect(name)}
              className={cn(
                "flex w-full items-center gap-2 px-4 py-2.5 text-sm transition-colors text-left",
                isCurrent && "bg-primary/10 text-primary",
                !isCurrent && !hasSelected && "hover:bg-muted/80",
                !isCurrent && hasSelected && "opacity-40",
                hasSelected && isCurrent && "font-medium",
                (disabled || hasSelected) && "cursor-default",
              )}
            >
              <span className={cn("shrink-0", isCurrent ? "text-primary" : "text-muted-foreground/40")}>
                {isCurrent ? "\u25CF" : "\u2003"}
              </span>
              <span className="flex-1 truncate">{name}</span>
              {isCurrent && (
                <span className="text-xs text-muted-foreground shrink-0">(当前选中)</span>
              )}
            </button>
          );
        })}
      </div>
      <div className="border-t">
        <button
          type="button"
          disabled={disabled || hasSelected || currentBook === null}
          onClick={handleClear}
          className={cn(
            "flex w-full items-center gap-2 px-4 py-2.5 text-sm transition-colors text-left text-muted-foreground",
            !hasSelected && currentBook !== null && "hover:bg-muted/80 hover:text-foreground",
            (hasSelected || currentBook === null) && "opacity-40 cursor-default",
          )}
        >
          <XIcon className="size-3.5 shrink-0" />
          <span>清除选择</span>
        </button>
      </div>
    </div>
  );
}
