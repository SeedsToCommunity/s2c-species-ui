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

---

## Production Config Persistence (Priority: Low)

Column configurations are stored in JSON files in the `config/` directory. Currently, when the app is republished, the development workspace files overwrite whatever was running in production.

### Current Behavior
- Column configs live in `config/display_columns.json` and `config/screen_*.json`
- Changes made in development are saved to these files
- On republish, production gets the current workspace files
- Any column ordering done directly in production would be lost (but production admin is disabled anyway)

### Potential Future Solutions
If production-specific configs become needed:

1. **Database storage with environment separation** - Store configs in PostgreSQL with an `environment` column so dev and prod have separate rows. Both persist across deploys.

2. **Export/sync workflow** - Before republishing, export production configs back to workspace files.

3. **Production-only editing** - Only allow config editing in production, making it the sole source of truth.

### Current Workaround
Make all column config changes in the development workspace, verify they look correct, then republish. The development configs become the production configs.

### Related Files
- `config/display_columns.json` - Main page column configuration
- `config/screen_*.json` - Species detail screen configurations
- `app.py` - load_display_config(), load_screen_config()
