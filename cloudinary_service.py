"""
Cloudinary service module for fetching and caching species images.
"""
import os
import json
import logging
import hashlib
from datetime import datetime, timedelta
from pathlib import Path

import cloudinary
import cloudinary.api
import cloudinary.uploader
from cloudinary.search import Search

# Configure logging
logging.basicConfig(level=logging.DEBUG)
logger = logging.getLogger(__name__)

# Global cache
_image_cache = {}
_cache_timestamps = {}

def is_development_mode():
    """Check if running in development mode (caching disabled for testing)"""
    env = os.environ.get('REPLIT_ENVIRONMENT', 'development')
    return env == 'development'

def configure_cloudinary():
    """Configure Cloudinary with environment credentials"""
    cloud_name = os.environ.get('CLOUDINARY_CLOUD_NAME')
    api_key = os.environ.get('CLOUDINARY_API_KEY')
    api_secret = os.environ.get('CLOUDINARY_API_SECRET')
    
    if not all([cloud_name, api_key, api_secret]):
        logger.warning("Cloudinary credentials not fully configured")
        return False
    
    cloudinary.config(
        cloud_name=cloud_name,
        api_key=api_key,
        api_secret=api_secret,
        secure=True
    )
    logger.info(f"Cloudinary configured for cloud: {cloud_name}")
    return True

def load_image_sources_config():
    """Load image sources configuration"""
    config_path = Path('config/cloudinary_config.json')
    if not config_path.exists():
        logger.warning("Image sources config not found")
        return {}
    
    try:
        with open(config_path, 'r') as f:
            return json.load(f)
    except Exception as e:
        logger.error(f"Error loading image sources config: {e}")
        return {}

def get_cache_path():
    """Get the cache directory path"""
    config = load_image_sources_config()
    cache_dir = config.get('cache_settings', {}).get('cache_directory', '/tmp/cloudinary_cache')
    Path(cache_dir).mkdir(parents=True, exist_ok=True)
    return Path(cache_dir)

def get_cache_ttl_hours():
    """Get cache TTL in hours"""
    config = load_image_sources_config()
    return config.get('cache_settings', {}).get('ttl_hours', 24)

def get_config_version():
    """Get a version hash of the config file based on modification time and content"""
    config_path = Path('config/cloudinary_config.json')
    if not config_path.exists():
        return "no_config"
    try:
        # Use file modification time + size as version
        stat = config_path.stat()
        version_str = f"{stat.st_mtime}_{stat.st_size}"
        return hashlib.md5(version_str.encode()).hexdigest()[:8]
    except Exception:
        return "unknown"

def create_cache_key(genus, species, image_group_id):
    """Create a unique cache key for a species/image group combination.
    
    Includes config version so cache is invalidated when config changes.
    """
    config_version = get_config_version()
    key_str = f"{genus}_{species}_{image_group_id}_{config_version}".lower()
    return hashlib.md5(key_str.encode()).hexdigest()

def load_cache_from_disk(cache_key):
    """Load cached images from disk"""
    cache_file = get_cache_path() / f"{cache_key}.json"
    
    if not cache_file.exists():
        return None
    
    try:
        with open(cache_file, 'r') as f:
            data = json.load(f)
        
        # Check if cache is still valid
        cached_time = datetime.fromisoformat(data.get('cached_at', '2000-01-01'))
        ttl = timedelta(hours=get_cache_ttl_hours())
        
        if datetime.now() - cached_time > ttl:
            logger.debug(f"Cache expired for {cache_key}")
            return None
        
        return data.get('images', [])
    except Exception as e:
        logger.error(f"Error loading cache from disk: {e}")
        return None

def save_cache_to_disk(cache_key, images):
    """Save images to disk cache"""
    cache_file = get_cache_path() / f"{cache_key}.json"
    
    try:
        data = {
            'cached_at': datetime.now().isoformat(),
            'images': images
        }
        with open(cache_file, 'w') as f:
            json.dump(data, f)
        logger.debug(f"Saved {len(images)} images to cache: {cache_key}")
    except Exception as e:
        logger.error(f"Error saving cache to disk: {e}")

def clear_image_cache():
    """Clear all image caches"""
    global _image_cache, _cache_timestamps
    _image_cache = {}
    _cache_timestamps = {}
    
    # Also clear disk cache
    cache_path = get_cache_path()
    try:
        for cache_file in cache_path.glob("*.json"):
            cache_file.unlink()
        logger.info("Cleared all image caches")
    except Exception as e:
        logger.error(f"Error clearing disk cache: {e}")

def build_search_expression(genus, species, image_group, include_genus_fallback=False, exclude_pending=True):
    """Build Cloudinary search expression for a species and image group.
    
    Implements a multi-tier matching strategy:
    1. Species filename pattern (e.g., asclepias_tuberosa*)
    2. Species tag (e.g., species:asclepias_tuberosa)
    3. Genus filename pattern (fallback, e.g., asclepias_*)
    4. Genus tag (fallback, e.g., genus:asclepias)
    
    If image_group has specific tags, those are combined with species matching.
    If exclude_pending is True, images with 'Pending' tag are excluded.
    """
    config = load_image_sources_config()
    matching = config.get('species_matching', {})
    
    # Normalize species/genus names
    genus_clean = genus.lower().strip() if genus else ''
    species_clean = species.lower().strip().replace(' ', '_') if species else ''
    species_pattern = f"{genus_clean}_{species_clean}"
    
    # Build species-level expressions
    species_matches = []
    
    # 1. Filename matching for species
    species_matches.append(f"filename:{species_pattern}*")
    
    # 2. Tag-based matching for species (quote tag values with special chars)
    tag_format = matching.get('tag_format', 'species:{genus}_{species}')
    species_tag = tag_format.format(genus=genus_clean, species=species_clean)
    species_matches.append(f'tags="{species_tag}"')
    
    # Species expression: either filename OR species tag
    species_expr = '(' + ' OR '.join(species_matches) + ')'
    
    # Build genus fallback expressions
    genus_matches = []
    if include_genus_fallback and matching.get('fallback_to_genus', True):
        # 3. Filename matching for genus (any species in this genus)
        genus_matches.append(f"filename:{genus_clean}_*")
        
        # 4. Genus tag (quote tag values with special chars)
        genus_tag_format = matching.get('genus_tag_format', 'genus:{genus}')
        genus_tag = genus_tag_format.format(genus=genus_clean)
        genus_matches.append(f'tags="{genus_tag}"')
    
    # Combine species and genus expressions
    if genus_matches:
        genus_expr = '(' + ' OR '.join(genus_matches) + ')'
        primary_expr = f"({species_expr} OR {genus_expr})"
    else:
        primary_expr = species_expr
    
    # Get image group specific tags
    group_tags = image_group.get('tags', [])
    
    # Build the base expression
    if group_tags:
        tag_conditions = ' AND '.join([f'tags="{tag}"' for tag in group_tags])
        base_expr = f"{primary_expr} AND ({tag_conditions})"
    else:
        base_expr = primary_expr
    
    # Exclude pending images if requested
    if exclude_pending:
        return f"({base_expr}) AND -tags=\"Pending\""
    else:
        return base_expr

def search_cloudinary_images(genus, species, image_group_id, force_refresh=False, include_genus_fallback=True):
    """Search Cloudinary for images matching a species and image group.
    
    Uses multi-tier matching strategy:
    1. Species filename pattern
    2. Species tag
    3. Genus filename pattern (if include_genus_fallback=True and no species results)
    4. Genus tag (if include_genus_fallback=True and no species results)
    """
    if not configure_cloudinary():
        return []
    
    config = load_image_sources_config()
    image_groups = config.get('image_groups', {})
    
    if image_group_id not in image_groups:
        logger.warning(f"Unknown image group: {image_group_id}")
        return []
    
    image_group = image_groups[image_group_id]
    
    # Check cache first (skip in development mode for testing)
    cache_key = create_cache_key(genus, species, image_group_id)
    dev_mode = is_development_mode()
    
    if dev_mode:
        logger.debug(f"Development mode: skipping cache for {genus} {species} / {image_group_id}")
    
    if not force_refresh and not dev_mode:
        # Check memory cache
        if cache_key in _image_cache:
            cache_time = _cache_timestamps.get(cache_key, datetime.min)
            if datetime.now() - cache_time < timedelta(hours=get_cache_ttl_hours()):
                logger.debug(f"Using memory cache for {genus} {species} / {image_group_id}")
                return _image_cache[cache_key]
        
        # Check disk cache
        cached = load_cache_from_disk(cache_key)
        if cached is not None:
            _image_cache[cache_key] = cached
            _cache_timestamps[cache_key] = datetime.now()
            logger.debug(f"Using disk cache for {genus} {species} / {image_group_id}")
            return cached
    
    # Search Cloudinary
    try:
        # Get resource type from config (defaults to 'image')
        matching = config.get('species_matching', {})
        resource_type = matching.get('resource_type', 'image')
        
        # Build search expression using improved matching logic
        # First try without genus fallback
        search_expr = build_search_expression(genus, species, image_group, include_genus_fallback=False)
        # Filter by resource type from config
        search_expr = f"resource_type:{resource_type} AND ({search_expr})"
        
        logger.info(f"Cloudinary search (species-level): {search_expr}")
        
        search = Search()
        search.expression(search_expr)
        search.max_results(50)
        search.with_field('tags')
        search.with_field('context')
        
        result = search.execute()
        resources = result.get('resources', [])
        
        # If no results and fallback enabled, try with genus fallback
        if not resources and include_genus_fallback:
            fallback_expr = build_search_expression(genus, species, image_group, include_genus_fallback=True)
            # Filter by resource type from config
            fallback_expr = f"resource_type:{resource_type} AND ({fallback_expr})"
            logger.info(f"No species results, trying genus fallback: {fallback_expr}")
            
            search = Search()
            search.expression(fallback_expr)
            search.max_results(50)
            search.with_field('tags')
            search.with_field('context')
            
            result = search.execute()
            resources = result.get('resources', [])
        
        images = []
        # Valid image formats to include
        valid_image_formats = {'jpg', 'jpeg', 'png', 'gif', 'webp', 'svg', 'bmp', 'tiff', 'tif', 'heic', 'avif'}
        
        for resource in resources:
            # Skip non-image formats (backup filter)
            file_format = resource.get('format', '').lower()
            if file_format and file_format not in valid_image_formats:
                logger.debug(f"Skipping non-image file: {resource.get('public_id')} (format: {file_format})")
                continue
                
            img_data = {
                'public_id': resource.get('public_id'),
                'url': resource.get('secure_url'),
                'format': resource.get('format'),
                'width': resource.get('width'),
                'height': resource.get('height'),
                'tags': resource.get('tags', []),
                'filename': resource.get('filename', ''),
                'created_at': resource.get('created_at'),
                'context': resource.get('context', {})
            }
            
            # Generate thumbnail URL - uniform height, no cropping, no upscaling
            img_data['thumbnail_url'] = cloudinary.CloudinaryImage(resource.get('public_id')).build_url(
                height=200, crop='limit', quality='auto'
            )
            
            # Generate medium size URL
            img_data['medium_url'] = cloudinary.CloudinaryImage(resource.get('public_id')).build_url(
                width=800, height=600, crop='limit', quality='auto'
            )
            
            images.append(img_data)
        
        logger.info(f"Found {len(images)} images for {genus} {species} / {image_group_id}")
        
        # Cache results
        _image_cache[cache_key] = images
        _cache_timestamps[cache_key] = datetime.now()
        save_cache_to_disk(cache_key, images)
        
        return images
        
    except Exception as e:
        logger.error(f"Error searching Cloudinary: {e}")
        return []

def get_species_images(genus, species, screen_id=None, force_refresh=False):
    """Get all images for a species, optionally filtered by screen"""
    config = load_image_sources_config()
    image_groups = config.get('image_groups', {})
    
    result = {}
    
    for group_id, group_config in image_groups.items():
        # If screen_id specified, only get groups assigned to that screen
        if screen_id:
            default_screens = group_config.get('default_screens', [])
            if screen_id not in default_screens:
                continue
        
        images = search_cloudinary_images(genus, species, group_id, force_refresh)
        if images:
            result[group_id] = {
                'label': group_config.get('label', group_id),
                'icon': group_config.get('icon', 'image'),
                'display_style': group_config.get('display_style', 'gallery'),
                'images': images
            }
    
    return result

def get_all_image_groups():
    """Get all configured image groups for admin UI"""
    config = load_image_sources_config()
    image_groups = config.get('image_groups', {})
    
    result = {}
    for group_id, group_config in image_groups.items():
        result[group_id] = {
            'id': group_id,
            'label': group_config.get('label', group_id),
            'description': group_config.get('description', ''),
            'icon': group_config.get('icon', 'image'),
            'tags': group_config.get('tags', []),
            'default_screens': group_config.get('default_screens', []),
            'type': 'image_group'
        }
    
    return result

def test_cloudinary_connection():
    """Test if Cloudinary connection is working"""
    if not configure_cloudinary():
        return {'success': False, 'error': 'Credentials not configured'}
    
    try:
        # Try a simple API call
        result = cloudinary.api.ping()
        return {'success': True, 'status': result.get('status', 'ok')}
    except Exception as e:
        return {'success': False, 'error': str(e)}

def upload_user_image(file_data, genus, species, username, tags=None):
    """Upload a user-submitted image to Cloudinary.
    
    Args:
        file_data: File object or bytes
        genus: Plant genus
        species: Plant species  
        username: Submitter's name (sanitized)
        tags: List of additional tags (e.g., ['seeds', 'seedling'])
    
    Returns:
        dict with 'success', 'url', 'public_id', or 'error'
    """
    if not configure_cloudinary():
        return {'success': False, 'error': 'Cloudinary not configured'}
    
    try:
        import cloudinary.uploader
        
        # Sanitize inputs for filename
        genus_clean = genus.lower().strip().replace(' ', '_')
        species_clean = species.lower().strip().replace(' ', '_')
        username_clean = ''.join(c for c in username.lower() if c.isalnum() or c == '_').strip('_')
        if not username_clean:
            username_clean = 'anonymous'
        
        # Create timestamp
        timestamp = datetime.now().strftime('%Y%m%d_%H%M%S')
        
        # Build filename (public_id without folder)
        filename = f"{genus_clean}_{species_clean}_{username_clean}_{timestamp}"
        
        # Build tags list - species info is in filename only, not as a tag
        upload_tags = ['UserSubmitted', 'Pending']
        if tags:
            upload_tags.extend(tags)
        
        logger.info(f"Uploading image to Cloudinary folder 'UserSubmitted': {filename}")
        logger.info(f"Tags: {upload_tags}")
        
        # Upload to Cloudinary with asset_folder (keeps public_id clean without folder prefix)
        result = cloudinary.uploader.upload(
            file_data,
            public_id=filename,
            asset_folder="UserSubmitted",
            tags=upload_tags,
            context={
                'submitter': username,
                'genus': genus,
                'species': species,
                'submitted_at': datetime.now().isoformat()
            },
            resource_type='image'
        )
        
        logger.info(f"Upload successful: {result.get('secure_url')}")
        
        return {
            'success': True,
            'url': result.get('secure_url'),
            'public_id': result.get('public_id'),
            'tags': upload_tags
        }
        
    except Exception as e:
        logger.error(f"Error uploading to Cloudinary: {e}")
        return {'success': False, 'error': str(e)}

def get_pending_images(limit=50):
    """Get all images with 'Pending' tag, sorted by newest first.
    
    Returns list of image dicts with metadata.
    """
    if not configure_cloudinary():
        return []
    
    try:
        search = Search()
        search.expression('tags="Pending" AND tags="UserSubmitted"')
        search.sort_by('created_at', 'desc')
        search.max_results(limit)
        search.with_field('tags')
        search.with_field('context')
        
        result = search.execute()
        resources = result.get('resources', [])
        
        images = []
        for resource in resources:
            # Parse genus/species from public_id filename
            # Format: UserSubmitted/genus_species_username_timestamp
            public_id = resource.get('public_id', '')
            genus = ''
            species = ''
            submitter = 'Unknown'
            
            # Extract filename from public_id (remove folder prefix)
            filename = public_id.split('/')[-1] if '/' in public_id else public_id
            parts = filename.split('_')
            if len(parts) >= 4:
                # genus_species_username_timestamp
                genus = parts[0].capitalize()
                species = parts[1]
                # Username might have underscores, timestamp is last part
                # Find the timestamp (format: YYYYMMDD_HHMMSS)
                for i in range(len(parts) - 1, 1, -1):
                    if len(parts[i]) == 6 and parts[i].isdigit():  # HHMMSS
                        if i > 0 and len(parts[i-1]) == 8 and parts[i-1].isdigit():  # YYYYMMDD
                            submitter = '_'.join(parts[2:i-1]) if i > 3 else parts[2]
                            break
                else:
                    submitter = parts[2] if len(parts) > 2 else 'Unknown'
            
            # Format submitter name nicely
            submitter = submitter.replace('_', ' ').title()
            
            img_data = {
                'public_id': public_id,
                'url': resource.get('secure_url'),
                'thumbnail_url': cloudinary.CloudinaryImage(public_id).build_url(
                    height=200, crop='limit', quality='auto'
                ),
                'format': resource.get('format'),
                'width': resource.get('width'),
                'height': resource.get('height'),
                'tags': resource.get('tags', []),
                'created_at': resource.get('created_at'),
                'submitter': submitter,
                'genus': genus,
                'species': species
            }
            images.append(img_data)
        
        logger.info(f"Found {len(images)} pending images")
        return images
        
    except Exception as e:
        logger.error(f"Error fetching pending images: {e}")
        return []

def approve_image(public_id):
    """Remove 'Pending' tag from an image to approve it.
    
    Returns dict with 'success' or 'error'.
    """
    if not configure_cloudinary():
        return {'success': False, 'error': 'Cloudinary not configured'}
    
    try:
        result = cloudinary.uploader.remove_tag('Pending', [public_id])
        logger.info(f"Approved image: {public_id}")
        return {'success': True, 'result': result}
    except Exception as e:
        logger.error(f"Error approving image: {e}")
        return {'success': False, 'error': str(e)}

def delete_image(public_id):
    """Delete an image from Cloudinary.
    
    Returns dict with 'success' or 'error'.
    """
    if not configure_cloudinary():
        return {'success': False, 'error': 'Cloudinary not configured'}
    
    try:
        result = cloudinary.uploader.destroy(public_id)
        logger.info(f"Deleted image: {public_id}")
        return {'success': True, 'result': result}
    except Exception as e:
        logger.error(f"Error deleting image: {e}")
        return {'success': False, 'error': str(e)}

def get_approved_images_for_species(genus, species, limit=50):
    """Get ALL non-pending images for a specific species.
    
    Returns list of image dicts. Used by admin review to show existing images.
    """
    if not configure_cloudinary():
        return []
    
    try:
        genus_clean = genus.lower().strip()
        species_clean = species.lower().strip().replace(' ', '_')
        species_tag = f"species:{genus_clean}_{species_clean}"
        
        search = Search()
        # ALL images for this species (by tag or filename), excluding pending
        search.expression(f'resource_type:image AND ((filename:{genus_clean}_{species_clean}* OR tags="{species_tag}")) AND -tags="Pending"')
        search.sort_by('created_at', 'desc')
        search.max_results(limit)
        search.with_field('tags')
        search.with_field('context')
        
        result = search.execute()
        resources = result.get('resources', [])
        
        images = []
        for resource in resources:
            context = resource.get('context', {})
            custom = context.get('custom', {})
            
            img_data = {
                'public_id': resource.get('public_id'),
                'url': resource.get('secure_url'),
                'thumbnail_url': cloudinary.CloudinaryImage(resource.get('public_id')).build_url(
                    height=200, crop='limit', quality='auto'
                ),
                'format': resource.get('format'),
                'tags': resource.get('tags', []),
                'created_at': resource.get('created_at'),
                'submitter': custom.get('submitter', '')
            }
            images.append(img_data)
        
        logger.info(f"Found {len(images)} approved images for {genus} {species}")
        return images
        
    except Exception as e:
        logger.error(f"Error fetching approved images: {e}")
        return []
