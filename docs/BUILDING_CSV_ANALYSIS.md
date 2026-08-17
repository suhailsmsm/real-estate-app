# Building CSVs — usefulness analysis

Assessment of the two `data/raw` files against what we need, prepared 2026-07-24.

| File | Rows | What it is |
|---|---|---|
| `buildings_2026-07-23…` | 251,642 | One row per **building**; ties building → project → parcel, plus size/rooms/floors |
| `building_summary_information_2026-06-29…` | 529,717 | Building **permit/construction** register: height, status, completion, green-building, plot |

## Bottom line

**Neither file helps geolocation** — no coordinates, no Makani, no lat/long. So the Makani building-geocoding plan is **unchanged**; these files can't place anything on the map. Their value is **attribute enrichment**, and there `buildings_…` is genuinely useful: it joins to **54% of our projects** and carries real building attributes.

## Against the three things you hoped for

1. **More robust building names → No.** There is *no* building-name column. Buildings are identified only by **codes** — `building_number` like `"RM2 Mira Oasis II-V-232"`, `"Q645"`, `"MA0230"`. Those are useless as Makani search terms. The transactions' `building_name_en` (`"LAKE TERRACE"`) remains the best geocoding key; these CSVs don't improve it.
2. **Economy/luxury class → Not directly, but derivable.** No quality-tier column. The closest is `building_type_english` — but that's *usage*, not luxury: Investment Villa (192k), Private Villa (170k), Multi Storey (53k), Public Building (49k), Industrial (42k). A real economy↔luxury tier would be **derived** from these attributes (built-up area per unit, height, `is_green_building`, facilities) **combined with our own price/m²** — which we already have. Worth doing as an analytics feature, but it's not a field we can just read.
3. **Bedrooms → Partially.** `buildings_…` has `rooms_en` populated 87% (`4 B/R`, `3 B/R`, `5 bed rooms+hall`…). But every row is `property_type = "Building"`, and this is the building's *representative* room count — meaningful for **villa-type buildings (one dwelling)**, but it does **not** capture the bedroom *mix* of a multi-unit tower. Per-unit bedrooms already live on our transactions (`rooms`). So this is a modest add for villas, not a new capability.

## The join reality (decides usability)

- **`project_name_en` + area is the viable key.** 1,980 of the CSV's 2,089 projects (**94%**) match our `dim_project` by name; areas match **99%**. Those 1,980 cover **54% of our 3,616 projects**.
- **`parcel_id` is NOT a usable bridge.** The CSVs are 98–100% parcel-populated, but our `fact_sale_transaction` carries `parcel_id` on only **1,254 of 1.75M** rows. So we cannot join on parcel — it has to be name+area.
- **`building_summary_information` is the weaker of the two for us**: it's permit-level (159k `project_no`, no project *name*, no building name), keyed by parcel/community — i.e. joinable mainly by `parcel_id`, which we lack. It overlaps `buildings_…` on the physical attributes anyway. Lower priority.

## Genuinely useful attributes (from `buildings_…`, name+area-joinable)

Per building, high coverage: `built_up_area` (92%), `floors` (84%), `rooms_en` (87%), `car_parks`, `elevators`, `swimming_pools`, `offices`, `shops`, `flats`, `master_project_en` (50%), `is_free_hold`. From `building_summary` (if we later solve the parcel join): `building_height`, `building_completion_date`/`construction_year`, `building_status` (New/Delivered/Expired/Cancelled), `is_green_building`, `plot_area`, `no_of_lifts`.

## Recommendation

1. **Keep the Makani geolocation plan as-is** — these files don't touch it and aren't on its critical path.
2. **Add the building CSVs later as a separate, optional attribute-enrichment importer**, joined by **(project_name, area)**, populating `dim_building`/`dim_project` attributes (size, floors, facilities, freehold, status). One-off + monthly refresh, non-fatal, like the other data.dubai imports — *after* the geolocation work lands.
3. **Derive an economy↔luxury tier as an analytics feature** from these attributes + our price/m², rather than expecting a class column. Flag as follow-on.
4. **Deprioritize `building_summary_information`** until/unless we obtain `parcel_id` on our facts (it's the only clean key into it).
