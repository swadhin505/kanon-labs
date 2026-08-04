import Link from "next/link";

export default function NotFound() {
  return <div className="page-shell empty-state"><h1>Scenario not found</h1><Link href="/">Return to overview</Link></div>;
}
