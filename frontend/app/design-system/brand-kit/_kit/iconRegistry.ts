import type { LucideIcon } from "lucide-react";
import {
  Activity, AlertCircle, AlertTriangle, Archive, ArrowDown, ArrowLeft, ArrowLeftRight, ArrowRight, ArrowUp, ArrowUpDown, ArrowUpRight,
  BadgeCheck, Ban, Bell, Bot, Braces, Building, Building2, CalendarRange, Check, CheckCircle2, ChevronDown, ChevronLeft, ChevronRight,
  CircleDashed, ClipboardCheck, Clock, Component, Copy, Database, Download, EqualNot, Eye, EyeOff, FileCheck2, FileText, FileUp, Files,
  Flag, FlaskConical, FolderOpen, GitBranch, Globe2, HelpCircle, History, Inbox, Info, KeyRound, Layers, LayoutDashboard, LayoutTemplate,
  Library, Link2, Loader2, Lock, LogOut, Menu, MessageSquareText, Minus, MinusCircle, Moon, MoreHorizontal, Package, PackageCheck, Palette,
  PanelLeftClose, PanelLeftOpen, PauseCircle, PenLine, Pencil, PlayCircle, Plug, Plus, RefreshCw, RotateCcw, Save, Scale, ScanSearch,
  ScrollText, Search, Send, ServerCog, Settings2, ShieldAlert, ShieldCheck, ShieldOff, SlidersHorizontal, Sun, Tags, Trash2, Undo2, Upload,
  User, UserCheck, UserCog, Users, Wrench, X, XCircle, Image as ImageIcon,
} from "lucide-react";

/* The icon gallery: lucide-react (the application's only icon library, already a dependency), limited to icons the
   application already imports, each in one category with the use it already has. `where` names the screens or
   components that use it today (scanned from the source on 4 Nov 2026). Nothing here adds an icon to a screen. */

export const CATEGORIES = [
  "Navigation", "Organization", "Platform administration", "Compliance & regulations", "Evidence & documents",
  "Approvals & attestations", "Security & permissions", "Status & alerts", "Actions & utilities",
] as const;
export type Category = (typeof CATEGORIES)[number];
export type IconEntry = { name: string; Icon: LucideIcon; category: Category; usage: string; where: string };

const e = (name: string, Icon: LucideIcon, category: Category, usage: string, where: string): IconEntry => ({ name, Icon, category, usage, where });

export const ICONS: IconEntry[] = [
  // Navigation
  e("LayoutDashboard", LayoutDashboard, "Navigation", "Dashboards and area overviews", "Sidebar Overview / Dashboard; overview tabs"),
  e("Menu", Menu, "Navigation", "Open the navigation drawer on small screens", "Application header"),
  e("PanelLeftClose", PanelLeftClose, "Navigation", "Collapse the sidebar", "Application sidebar"),
  e("PanelLeftOpen", PanelLeftOpen, "Navigation", "Expand the sidebar", "Application sidebar"),
  e("ChevronDown", ChevronDown, "Navigation", "Open a menu or disclosure", "Dropdowns, collapsible panels"),
  e("ChevronRight", ChevronRight, "Navigation", "Next item, breadcrumb separator", "Pagination, breadcrumbs"),
  e("ChevronLeft", ChevronLeft, "Navigation", "Previous item", "Pagination"),
  e("ArrowLeft", ArrowLeft, "Navigation", "Back to the previous screen", "Design Governance “Back to workspace”"),
  e("ArrowRight", ArrowRight, "Navigation", "Go to a related screen or next step", "Pending actions, step buttons"),
  e("ArrowUpRight", ArrowUpRight, "Navigation", "Opens another screen (outbound link)", "Flow links, application screen links"),
  e("Search", Search, "Navigation", "Search and “Jump to”", "Application and Design Governance headers"),
  e("MoreHorizontal", MoreHorizontal, "Navigation", "Overflow menu of further actions", "Component library menus"),
  e("Inbox", Inbox, "Navigation", "Regulatory Inbox / Change Radar", "Sidebar"),
  e("History", History, "Navigation", "Historical reconstruction; traceable history", "Sidebar, High-Assurance Flows principles"),
  e("LayoutTemplate", LayoutTemplate, "Navigation", "Design system foundations", "Design Governance sidebar"),
  e("Component", Component, "Navigation", "Component library", "Design Governance sidebar"),
  e("Image", ImageIcon, "Navigation", "Icons & Brand Kit (brand assets)", "Design Governance sidebar"),
  // Organization
  e("Building2", Building2, "Organization", "The customer organization; the Customer Assurance Workspace plane", "Plane badge, Entities & Scope, Org Workspace"),
  e("Building", Building, "Organization", "A tenant or legal entity record", "Super Admin tenants, application header context"),
  e("Users", Users, "Organization", "People and roles", "Users & Roles / Team & Access"),
  e("User", User, "Organization", "One person", "Evidence provenance"),
  e("UserCog", UserCog, "Organization", "Account settings", "Account menu"),
  e("Globe2", Globe2, "Organization", "Cells, regions and residency", "Cells & Residency / Org Settings"),
  e("CalendarRange", CalendarRange, "Organization", "Assurance and reporting periods", "Assurance Periods / Assurance Reports"),
  e("Bot", Bot, "Organization", "AI and agent assets", "AI/Agent Asset Dossiers / Agent Governance"),
  // Platform administration
  e("ServerCog", ServerCog, "Platform administration", "The Platform Control Plane", "Plane badge, Super Admin"),
  e("Activity", Activity, "Platform administration", "Service health and live signals", "Super Admin service health"),
  e("Database", Database, "Platform administration", "Data stores, tenant metadata, IPE", "Super Admin metadata, IPE"),
  e("Plug", Plug, "Platform administration", "Connectors and integrations", "Connectors / Integrations"),
  e("Settings2", Settings2, "Platform administration", "Settings", "Component library, Org Workspace settings tab"),
  e("Layers", Layers, "Platform administration", "Architecture and planes", "Foundations architecture panel"),
  e("PackageCheck", PackageCheck, "Platform administration", "Released content or a built package", "Content Releases, assurance package flow"),
  // Compliance & regulations
  e("ClipboardCheck", ClipboardCheck, "Compliance & regulations", "Obligations", "Sidebar Obligations; Org Workspace"),
  e("SlidersHorizontal", SlidersHorizontal, "Compliance & regulations", "Controls and tests", "Sidebar Controls & Testing; Org Workspace"),
  e("Scale", Scale, "Compliance & regulations", "Applicability and legal decisions", "Applicability Review / Decisions & Attestations"),
  e("Library", Library, "Compliance & regulations", "Regulatory sources and provisions", "Sources & Provisions"),
  e("Braces", Braces, "Compliance & regulations", "Rule authoring", "Rule Authoring"),
  e("FlaskConical", FlaskConical, "Compliance & regulations", "Rule test workbench", "Rule Test Workbench"),
  e("GitBranch", GitBranch, "Compliance & regulations", "Applicability trace", "Applicability Trace"),
  e("ScanSearch", ScanSearch, "Compliance & regulations", "Determination review", "High-Assurance Flows"),
  e("Flag", Flag, "Compliance & regulations", "Findings, exceptions and issues; also the legacy “review” status icon", "Sidebar Findings & Actions; StatusPill"),
  // Evidence & documents
  e("Archive", Archive, "Evidence & documents", "Evidence records", "Sidebar Evidence & IPE; Org Workspace"),
  e("FileText", FileText, "Evidence & documents", "A document; also Super Admin Planes in Design Governance", "Design Governance sidebar, evidence flow"),
  e("Files", Files, "Evidence & documents", "A set of documents", "Foundations architecture panel"),
  e("FileUp", FileUp, "Evidence & documents", "Submit evidence", "Evidence submission flow"),
  e("FileCheck2", FileCheck2, "Evidence & documents", "A verified file or source integrity", "Super Admin source integrity"),
  e("Upload", Upload, "Evidence & documents", "Upload a file", "File drop zone"),
  e("Download", Download, "Evidence & documents", "Download or export", "Export Library, package download"),
  e("Package", Package, "Evidence & documents", "Audit Package Builder", "Sidebar"),
  e("ScrollText", ScrollText, "Evidence & documents", "Audit and activity logs", "Super Admin administrative audit"),
  e("Link2", Link2, "Evidence & documents", "The ledger and linked records", "Sidebar Ledger / Audit Trail"),
  e("FolderOpen", FolderOpen, "Evidence & documents", "Open a collection", "Component library"),
  // Approvals & attestations
  e("UserCheck", UserCheck, "Approvals & attestations", "Human approval", "Approval flow, Org Workspace approvals"),
  e("PenLine", PenLine, "Approvals & attestations", "Attest or sign", "Attestation flow, Decisions"),
  e("BadgeCheck", BadgeCheck, "Approvals & attestations", "Verified or assured", "Foundations architecture panel"),
  e("Send", Send, "Approvals & attestations", "Submit for a decision", "Component library, evidence submission flow"),
  e("MessageSquareText", MessageSquareText, "Approvals & attestations", "A required reason or justification", "High-Assurance Flows principles"),
  e("Undo2", Undo2, "Approvals & attestations", "Revoke a decision", "Org Workspace approvals"),
  e("Pencil", Pencil, "Approvals & attestations", "Edit a draft", "Component library"),
  // Security & permissions
  e("ShieldCheck", ShieldCheck, "Security & permissions", "Protected, verified integrity, governance", "Verification buttons, Foundations"),
  e("ShieldAlert", ShieldAlert, "Security & permissions", "Exception, privileged access; the legacy “exception” status icon", "Privileged Access Log, flows, StatusPill"),
  e("ShieldOff", ShieldOff, "Security & permissions", "Separation of duties blocks an action", "Approval flow"),
  e("Lock", Lock, "Security & permissions", "Locked or under legal hold", "Retention & Legal Holds, evidence"),
  e("KeyRound", KeyRound, "Security & permissions", "Identity, credentials, JIT access", "Identity Integrations, Super Admin JIT"),
  e("Eye", Eye, "Security & permissions", "Reveal a value; also High-Assurance Flows", "Sign-in password field, Design Governance sidebar"),
  e("EyeOff", EyeOff, "Security & permissions", "Hide a value", "Sign-in password field"),
  e("LogOut", LogOut, "Security & permissions", "Sign out", "Account menu"),
  e("EqualNot", EqualNot, "Security & permissions", "The boundary between the two authority planes", "Foundations architecture panel"),
  // Status & alerts
  e("Check", Check, "Status & alerts", "Verified (design-system status icon)", "StatusBadge, StateBadge, toasts"),
  e("Clock", Clock, "Status & alerts", "Needs Review (design-system status icon)", "StatusBadge, StateBadge"),
  e("AlertTriangle", AlertTriangle, "Status & alerts", "Critical (design-system status icon)", "StatusBadge, StateBadge, warnings"),
  e("Ban", Ban, "Status & alerts", "Stale / Unknown (design-system status icon)", "StatusBadge, StateBadge"),
  e("Info", Info, "Status & alerts", "Informational (both status sets)", "StatusBadge, StatusPill, callouts"),
  e("CheckCircle2", CheckCircle2, "Status & alerts", "Good (legacy StatusPill icon)", "Application status pills"),
  e("CircleDashed", CircleDashed, "Status & alerts", "Warn (legacy StatusPill icon)", "Application status pills"),
  e("XCircle", XCircle, "Status & alerts", "Bad (legacy StatusPill icon)", "Application status pills"),
  e("MinusCircle", MinusCircle, "Status & alerts", "Neutral (legacy StatusPill icon)", "Application status pills"),
  e("HelpCircle", HelpCircle, "Status & alerts", "Unknown code (legacy StatusPill icon); also Fact Requests", "Status pills, sidebar"),
  e("AlertCircle", AlertCircle, "Status & alerts", "An error message", "Form errors"),
  e("Bell", Bell, "Status & alerts", "Notifications", "Application header"),
  e("Loader2", Loader2, "Status & alerts", "Work in progress (spins)", "Buttons, sidebar navigation"),
  // Actions & utilities
  e("Plus", Plus, "Actions & utilities", "Create or add", "Register an organisation or a legal entity"),
  e("X", X, "Actions & utilities", "Close or remove", "Dialogs, toasts, search clear"),
  e("Minus", Minus, "Actions & utilities", "Remove or indeterminate", "Checkboxes"),
  e("Copy", Copy, "Actions & utilities", "Copy a value", "Component library, this brand kit"),
  e("Save", Save, "Actions & utilities", "Save", "Component library, Org Workspace settings"),
  e("Wrench", Wrench, "Actions & utilities", "Correct or repair a value", "Determination review flow (fact correction)"),
  e("Trash2", Trash2, "Actions & utilities", "Delete", "Component library"),
  e("RefreshCw", RefreshCw, "Actions & utilities", "Reload or re-evaluate", "Controls, live checks"),
  e("RotateCcw", RotateCcw, "Actions & utilities", "Restart or reset", "Demo frames"),
  e("PlayCircle", PlayCircle, "Actions & utilities", "Start or resume; source runs", "Source Runs, tenant reactivation"),
  e("PauseCircle", PauseCircle, "Actions & utilities", "Pause or suspend", "Tenant suspension"),
  e("ArrowUpDown", ArrowUpDown, "Actions & utilities", "Sortable column", "Data tables"),
  e("ArrowUp", ArrowUp, "Actions & utilities", "Sorted ascending; upward change", "Data tables, KPI change"),
  e("ArrowDown", ArrowDown, "Actions & utilities", "Sorted descending; downward change", "Data tables, KPI change"),
  e("ArrowLeftRight", ArrowLeftRight, "Actions & utilities", "Compare or move between two values", "Super Admin tenant metadata"),
  e("Tags", Tags, "Actions & utilities", "Controlled terms and labels", "Component library, High-Assurance Flows principles"),
  e("Sun", Sun, "Actions & utilities", "Switch to the light theme", "Design Governance header"),
  e("Moon", Moon, "Actions & utilities", "Switch to the dark theme", "Design Governance header"),
  e("Palette", Palette, "Actions & utilities", "Colour", "Foundations"),
];

/** The proposed size scale (Area E), offered by the gallery's size switcher. */
export const SIZE_SCALE = [12, 16, 20, 24, 32] as const;
