import { redirect } from "next/navigation";

/** Administration & Trust has one page per area; the section root opens Users & Roles. */
export default function Admin() {
  redirect("/admin/users");
}
