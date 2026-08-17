/**
 * Listing entity descriptors — what each entity is, how to fetch it, which
 * columns it has and which filters apply.
 *
 * This is presentation metadata (labels, order, formatting, which controls to
 * show), which /openapi.json deliberately cannot supply. What it *does* supply
 * — the field and parameter names — was read straight out of the live schema,
 * so nothing here is guessed. `ColumnId` is checked against the generated
 * response types at compile time, so a field renamed in the API breaks the
 * build here rather than silently rendering blanks.
 *
 * `requiresOneOf` is load-bearing, not documentation: the facts endpoints
 * refuse an unfiltered scan with a 422, so the UI must guide the user into a
 * valid query instead of letting them discover the error.
 */

import type { components } from "./api-types";
import type { ListingEntity } from "./viewstate";

type Schemas = components["schemas"];

export type Row = Record<string, unknown>;

export type ColumnFormat =
  | "text"
  | "int"
  | "number"
  | "money"
  | "area"
  | "date"
  | "datetime"
  | "bool"
  | "pct";

export interface ColumnDef {
  id: string;
  label: string;
  format: ColumnFormat;
  /** Shown when no explicit column selection is stored in ViewState. */
  default?: boolean;
}

export interface FilterDef {
  /** Must match the API query-parameter name exactly. */
  id: string;
  label: string;
  kind: "text" | "number" | "date" | "bool" | "enum" | "entity";
  /** For kind "entity": which dimension to resolve against. */
  entity?: "area" | "project" | "building" | "developer";
  /** For kind "enum": fetched at runtime from /dimensions/usages etc. */
  options?: readonly string[];
  help?: string;
}

export interface EntityDescriptor {
  id: ListingEntity;
  label: string;
  /** Path only — the client prepends the base URL. */
  path: string;
  columns: ColumnDef[];
  filters: FilterDef[];
  /**
   * At least one of these must be set or the request 422s. Null means the
   * endpoint accepts an unfiltered listing (the small dimension tables).
   */
  requiresOneOf: string[] | null;
  /** Dimension endpoints count cheaply; fact endpoints return total: null. */
  hasTotal: boolean;
}

/** Compile-time guard: every column id must exist on the response model. */
type Cols<T> = (ColumnDef & { id: keyof T & string })[];

const txnColumns: Cols<Schemas["SaleTransaction"]> = [
  { id: "txn_date", label: "Date", format: "datetime", default: true },
  { id: "area_name_en", label: "Area", format: "text", default: true },
  { id: "project_name_en", label: "Project", format: "text", default: true },
  { id: "amount_aed", label: "Amount", format: "money", default: true },
  { id: "price_per_m2", label: "Price / m²", format: "money", default: true },
  { id: "actual_area_m2", label: "Area m²", format: "area", default: true },
  { id: "rooms", label: "Rooms", format: "text", default: true },
  { id: "usage", label: "Usage", format: "text", default: true },
  { id: "prop_type", label: "Type", format: "text" },
  { id: "prop_subtype", label: "Subtype", format: "text" },
  { id: "txn_group", label: "Group", format: "text" },
  { id: "procedure_name", label: "Procedure", format: "text" },
  { id: "is_offplan", label: "Off-plan", format: "bool" },
  { id: "is_freehold", label: "Freehold", format: "bool" },
  { id: "parking", label: "Parking", format: "text" },
  { id: "txn_number", label: "Txn no.", format: "text" },
  { id: "source_code", label: "Source", format: "text" },
  { id: "is_government", label: "Gov't", format: "bool" },
];

const rentColumns: Cols<Schemas["RentContract"]> = [
  { id: "start_date", label: "Start", format: "date", default: true },
  { id: "end_date", label: "End", format: "date" },
  { id: "area_name_en", label: "Area", format: "text", default: true },
  { id: "project_name_en", label: "Project", format: "text", default: true },
  { id: "annual_amount_aed", label: "Annual rent", format: "money", default: true },
  { id: "rent_per_m2_year", label: "Rent / m²·yr", format: "money", default: true },
  { id: "actual_area_m2", label: "Area m²", format: "area", default: true },
  { id: "rooms", label: "Rooms", format: "text", default: true },
  { id: "usage", label: "Usage", format: "text", default: true },
  { id: "version", label: "New / renew", format: "text" },
  { id: "prop_type", label: "Type", format: "text" },
  { id: "prop_subtype", label: "Subtype", format: "text" },
  { id: "contract_amount_aed", label: "Contract total", format: "money" },
  { id: "no_of_prop", label: "Properties", format: "int" },
  { id: "registration_date", label: "Registered", format: "datetime" },
  { id: "is_freehold", label: "Freehold", format: "bool" },
  { id: "source_code", label: "Source", format: "text" },
];

const buildingColumns: Cols<Schemas["Building"]> = [
  { id: "name_en", label: "Building", format: "text", default: true },
  { id: "area_name_en", label: "Area", format: "text", default: true },
  { id: "project_name_en", label: "Project", format: "text", default: true },
  { id: "floors", label: "Floors", format: "int", default: true },
  { id: "flats", label: "Flats", format: "int", default: true },
  { id: "built_up_area", label: "Built-up m²", format: "area", default: true },
  { id: "is_precise", label: "Precise geo", format: "bool", default: true },
  { id: "offices", label: "Offices", format: "int" },
  { id: "shops", label: "Shops", format: "int" },
  { id: "car_parks", label: "Car parks", format: "int" },
  { id: "elevators", label: "Elevators", format: "int" },
  { id: "swimming_pools", label: "Pools", format: "int" },
  { id: "rooms", label: "Rooms", format: "text" },
  { id: "is_free_hold", label: "Freehold", format: "bool" },
  { id: "geo_match_method", label: "Geo method", format: "text" },
  { id: "name_ar", label: "Name (AR)", format: "text" },
];

const projectColumns: Cols<Schemas["Project"]> = [
  { id: "name_en", label: "Project", format: "text", default: true },
  { id: "status", label: "Status", format: "text", default: true },
  { id: "percent_completed", label: "Complete", format: "pct", default: true },
  { id: "cnt_units", label: "Units", format: "int", default: true },
  { id: "completion_date", label: "Completion", format: "date", default: true },
  { id: "project_type", label: "Type", format: "text", default: true },
  { id: "is_master", label: "Master", format: "bool", default: true },
  { id: "master_project_en", label: "Master project", format: "text" },
  { id: "dld_project_number", label: "DLD no.", format: "text" },
  { id: "geo_building_count", label: "Buildings geocoded", format: "int" },
  { id: "geo_spread_m", label: "Geo spread m", format: "number" },
  { id: "geo_match_method", label: "Geo method", format: "text" },
  { id: "has_geo_data", label: "Has geo", format: "bool" },
  { id: "name_ar", label: "Name (AR)", format: "text" },
];

const areaColumns: Cols<Schemas["Area"]> = [
  { id: "name_en", label: "Area", format: "text", default: true },
  { id: "dld_area_code", label: "DLD code", format: "text", default: true },
  { id: "has_geo_data", label: "Has geo", format: "bool", default: true },
  { id: "has_boundary", label: "Has boundary", format: "bool", default: true },
  { id: "geo_match_method", label: "Geo method", format: "text", default: true },
  { id: "zone_name", label: "Zone", format: "text" },
  { id: "name_ar", label: "Name (AR)", format: "text" },
];

const usageFilter: FilterDef = {
  id: "usage",
  label: "Usage",
  kind: "enum",
  help: "Raw DLD vocabulary — load the real list rather than guessing.",
};

export const ENTITIES: Record<ListingEntity, EntityDescriptor> = {
  transactions: {
    id: "transactions",
    label: "Transactions",
    path: "/facts/transactions",
    columns: txnColumns,
    requiresOneOf: ["area_id", "building_id", "date_from", "project_id"],
    hasTotal: false,
    filters: [
      { id: "date_from", label: "From", kind: "date" },
      { id: "date_to", label: "To", kind: "date" },
      { id: "area_id", label: "Area", kind: "entity", entity: "area" },
      { id: "project_id", label: "Project", kind: "entity", entity: "project" },
      { id: "building_id", label: "Building", kind: "entity", entity: "building" },
      {
        id: "txn_group",
        label: "Group",
        kind: "enum",
        options: ["Sales", "Mortgage", "Gifts"],
        help: "Sales for price analysis — mortgages and gifts are not arm's-length prices.",
      },
      usageFilter,
      { id: "is_offplan", label: "Off-plan", kind: "bool" },
      { id: "price_min", label: "Min price", kind: "number" },
      { id: "price_max", label: "Max price", kind: "number" },
      { id: "area_m2_min", label: "Min m²", kind: "number" },
      { id: "area_m2_max", label: "Max m²", kind: "number" },
      { id: "rooms", label: "Rooms", kind: "text" },
    ],
  },
  rents: {
    id: "rents",
    label: "Rent contracts",
    path: "/facts/rents",
    columns: rentColumns,
    requiresOneOf: ["area_id", "project_id", "start_date_from"],
    hasTotal: false,
    filters: [
      { id: "start_date_from", label: "Starting from", kind: "date" },
      { id: "start_date_to", label: "Starting to", kind: "date" },
      { id: "area_id", label: "Area", kind: "entity", entity: "area" },
      { id: "project_id", label: "Project", kind: "entity", entity: "project" },
      {
        id: "version",
        label: "New / renew",
        kind: "enum",
        options: ["New", "Renew"],
      },
      usageFilter,
      { id: "annual_amount_min", label: "Min annual", kind: "number" },
      { id: "annual_amount_max", label: "Max annual", kind: "number" },
      {
        id: "include_future",
        label: "Include future starts",
        kind: "bool",
        help: "Leases can start years after registration.",
      },
    ],
  },
  buildings: {
    id: "buildings",
    label: "Buildings",
    path: "/dimensions/buildings",
    columns: buildingColumns,
    requiresOneOf: null,
    hasTotal: true,
    filters: [
      { id: "q", label: "Search name", kind: "text" },
      { id: "area_id", label: "Area", kind: "entity", entity: "area" },
      { id: "project_id", label: "Project", kind: "entity", entity: "project" },
      { id: "has_geo_data", label: "Has coordinates", kind: "bool" },
    ],
  },
  projects: {
    id: "projects",
    label: "Projects",
    path: "/dimensions/projects",
    columns: projectColumns,
    requiresOneOf: null,
    hasTotal: true,
    filters: [
      { id: "q", label: "Search name", kind: "text" },
      { id: "area_id", label: "Area", kind: "entity", entity: "area" },
      { id: "developer_id", label: "Developer", kind: "entity", entity: "developer" },
      { id: "status", label: "Status", kind: "text" },
      { id: "is_master", label: "Master only", kind: "bool" },
      { id: "has_geo_data", label: "Has coordinates", kind: "bool" },
    ],
  },
  areas: {
    id: "areas",
    label: "Areas",
    path: "/dimensions/areas",
    columns: areaColumns,
    requiresOneOf: null,
    hasTotal: true,
    filters: [
      { id: "q", label: "Search name", kind: "text" },
      { id: "has_geo_data", label: "Has coordinates", kind: "bool" },
    ],
  },
};

export function defaultColumns(entity: ListingEntity): string[] {
  return ENTITIES[entity].columns.filter((c) => c.default).map((c) => c.id);
}

/**
 * True when the entity needs a filter and none of the accepted ones is set.
 * Callers use this to render guidance and skip the request entirely, rather
 * than firing a request that is already known to 422.
 */
export function missingRequiredFilter(
  entity: ListingEntity,
  filters: Record<string, unknown>,
): boolean {
  const required = ENTITIES[entity].requiresOneOf;
  if (!required) return false;
  return !required.some((k) => filters[k] !== undefined && filters[k] !== "");
}
