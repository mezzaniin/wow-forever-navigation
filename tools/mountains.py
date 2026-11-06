import numpy as np
from PIL import Image
from scipy import ndimage as ndi
from masks import zone_mask
def features(mid):
    rgb = np.asarray(Image.open(f'maps/{mid}.png').convert('RGB')).astype(float)
    gray = rgb.mean(axis=2)
    local = ndi.uniform_filter(gray, 15)
    dark = gray < local - 18
    dens = ndi.uniform_filter(dark.astype(float), 11)
    r, g = rgb[...,0], rgb[...,1]
    warm = (r - g) > 12
    mount = (dens > 0.22) & ndi.uniform_filter(warm.astype(float), 11).__gt__(0.5)
    mount = ndi.binary_closing(mount, iterations=2)
    # schmale Strukturen (Straßen, ~5-9 px) entfernen, flächige Bergzeichnungen behalten
    st = ndi.generate_binary_structure(2, 1)
    core = ndi.binary_erosion(mount, structure=st, iterations=6)
    mount = ndi.binary_dilation(core, structure=st, iterations=7) & mount
    return mount, dark
if __name__ == '__main__':
    import sys
    for mid in sys.argv[1:]:
        cov, water = zone_mask(mid)
        mount, dark = features(mid)
        im = np.asarray(Image.open(f'maps/{mid}.png').convert('RGB')).copy()
        im[~cov] = (im[~cov]*0.3).astype(np.uint8)
        im[cov & water] = [40,80,255]
        m = cov & mount & ~water
        im[m] = (im[m]*0.4 + np.array([255,0,0])*0.6).astype(np.uint8)
        Image.fromarray(im).save(f'mask_{mid}.png'); print(mid, m.sum()/cov.sum())
