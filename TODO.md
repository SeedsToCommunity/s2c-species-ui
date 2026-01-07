# TODO - Plant Species Directory

## Mobile Admin Table Support (Priority: Medium)

The Column Usage Grid admin page needs a fundamental redesign for mobile usability.

### Current Problems
- Table is too long (80+ columns) - headers scroll out of view
- Users can't see which screen column they're clicking
- Small tap targets on mobile make interactions unreliable
- Click-only handlers don't work reliably on touch devices

### Recommended Solution: Screen-First Workflow
Replace the all-in-one matrix with a two-step workflow:

1. **Select a screen first** (Identification, Collection, Processing, etc.)
2. **Then manage columns for that screen** using a searchable list with toggles

Benefits:
- User always knows which screen they're configuring
- Column list can be filtered/searched
- Columns can be grouped by category
- Works naturally on mobile - just a scrollable list with toggles
- Consistent with existing "Reorder Columns" page pattern

### Implementation Notes
- Keep the matrix as a read-only summary view for desktop auditing
- Add sticky headers to the read-only matrix
- All editing should happen through the screen-first interface
- Add pointer-events handling and larger tap targets for any remaining interactive elements

### Related Files
- `templates/admin_column_usage.html` - Current table view
- `templates/admin_column_reorder.html` - Example of screen-first pattern
- `app.py` - Backend endpoints for column toggle/reorder
