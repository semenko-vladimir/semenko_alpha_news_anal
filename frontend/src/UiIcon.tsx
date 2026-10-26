type IconName = 'news' | 'search' | 'spark' | 'arrow' | 'chat';

const paths: Record<IconName, string> = {
  news: 'M7 5h13v15H7z M7 9H4v11h3 M10 8h7v4h-7z M10 15h7 M10 17h5',
  search: 'M17 17l4 4 M19 11a8 8 0 1 1-16 0 8 8 0 0 1 16 0',
  spark: 'M12 3l2.3 6.7L21 12l-6.7 2.3L12 21l-2.3-6.7L3 12l6.7-2.3z M20 2v4 M18 4h4',
  arrow: 'M5 12h14 M13 6l6 6-6 6',
  chat: 'M21 11.5a8.5 8.5 0 0 1-8.5 8.5H4l1.5-4A8.5 8.5 0 1 1 21 11.5z M8 10h8 M8 14h5',
};

export function UiIcon({ name, className = '' }: { name: IconName; className?: string }) {
  return <svg className={`ui-icon ${className}`} viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="1.7" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true" focusable="false"><path d={paths[name]} /></svg>;
}
