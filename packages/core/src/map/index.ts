interface Point { latitude: number; longitude: number }
interface Region { southwest: Point; northeast: Point }
/** 北向、无倾斜地图的墨卡托视野坐标；超出视野部分由容器裁切。 */
export function projectLayerImage(region: Region, bounds: { sw: Point; ne: Point }): Record<string, string> {
  const y = (lat: number) => Math.log(Math.tan(Math.PI / 4 + Math.max(-85, Math.min(85, lat)) * Math.PI / 360))
  const width = region.northeast.longitude - region.southwest.longitude
  const north = y(region.northeast.latitude)
  const height = north - y(region.southwest.latitude)
  if (!(width > 0 && height > 0)) throw new Error('地图视野无效，请缩放后重试')
  return {
    left: `${(bounds.sw.longitude - region.southwest.longitude) / width * 100}%`,
    top: `${(north - y(bounds.ne.latitude)) / height * 100}%`,
    width: `${(bounds.ne.longitude - bounds.sw.longitude) / width * 100}%`,
    height: `${(y(bounds.ne.latitude) - y(bounds.sw.latitude)) / height * 100}%`,
  }
}
