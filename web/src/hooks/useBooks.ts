import useSWR from "swr";
import { getApiBaseUrl } from "./utils";
import { getAuthHeader } from "../lib/auth";

export interface BookInfo {
  name: string;
  chapterCount: number;
}

interface UseBooksReturn {
  /** 书籍列表 */
  books: BookInfo[];
  /** 是否正在加载 */
  isLoading: boolean;
  /** 错误信息 */
  error: Error | undefined;
}

/**
 * 书籍列表 hook，SWR 缓存调用 GET /api/books。
 */
export function useBooks(): UseBooksReturn {
  const fetcher = async (url: string): Promise<BookInfo[]> => {
    const response = await fetch(url, {
      headers: getAuthHeader(),
    });
    if (!response.ok) {
      throw new Error("Failed to fetch books");
    }
    const data = await response.json();
    // 后端返回 snake_case，前端接口使用 camelCase，需要手动映射
    return data.map((item: Record<string, unknown>) => ({
      name: item.name,
      chapterCount: item.chapter_count,
    }));
  };

  const { data, error, isLoading } = useSWR<BookInfo[]>(
    `${getApiBaseUrl()}/api/books`,
    fetcher,
    {
      revalidateOnFocus: false,
      dedupingInterval: 30_000,
    },
  );

  return {
    books: data ?? [],
    isLoading,
    error,
  };
}
