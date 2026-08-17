/**
 * Fetches the current page for whichever entity is selected.
 *
 * Filter values already share their names with the API's query parameters
 * (viewstate.ts), so building the request is a filter-and-copy — never a
 * translation table that can drift from entities.ts.
 */

import { useQuery } from "@tanstack/react-query";
import { apiGet, type QueryValue } from "../../core/client";
import { ENTITIES, missingRequiredFilter, type Row } from "../../core/entities";
import type { Page } from "../../core/queries";
import type { ListingState } from "../../core/viewstate";

export function useListingQuery(listing: ListingState) {
  const entity = ENTITIES[listing.entity];
  // Facts endpoints 422 on an unfiltered scan; skip the request entirely
  // rather than let the user discover that from a failed fetch.
  const missing = missingRequiredFilter(listing.entity, listing.filters);

  const params: Record<string, QueryValue> = {
    ...(listing.filters as Record<string, QueryValue>),
    limit: listing.limit,
    offset: listing.offset,
  };

  const query = useQuery<Page<Row>>({
    queryKey: ["listing", listing.entity, listing.filters, listing.limit, listing.offset],
    queryFn: () => apiGet<Page<Row>>(entity.path, params),
    enabled: !missing,
    // Keeps the previous page's rows on screen while the next page loads,
    // instead of flashing an empty table on every click of Prev/Next.
    placeholderData: (prev) => prev,
  });

  return { entity, missing, ...query };
}
