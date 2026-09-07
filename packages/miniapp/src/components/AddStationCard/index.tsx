import { View, Text } from '@tarojs/components'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  onLocate?: () => void
  onManual?: () => void
}

/** 添加站点卡。两种录入方式见 docs/01 §六 */
export function AddStationCard({ onLocate, onManual }: Props) {
  return (
    <View className="add-station">
      <View className="add-station__head">
        <View className="add-station__plus">
          <Icon name="plus" size={20} color="#1677ff" />
        </View>
        <View className="add-station__text">
          <Text className="add-station__title">添加站点</Text>
          <Text className="add-station__sub">支持多种方式快速添加站点</Text>
        </View>
      </View>

      <View className="add-station__ways">
        <View className="add-station__way" onClick={onLocate}>
          <View className="add-station__way-icon add-station__way-icon--blue">
            <Icon name="mapPin" size={16} color="#1677ff" />
          </View>
          <View className="add-station__way-text">
            <Text className="add-station__way-title">获取当前位置</Text>
            <Text className="add-station__way-sub">自动获取经纬度</Text>
          </View>
          <Icon name="chevronRight" size={13} color="#9ca3af" />
        </View>

        <View className="add-station__way" onClick={onManual}>
          <View className="add-station__way-icon add-station__way-icon--green">
            <Icon name="navigation" size={16} color="#16a34a" />
          </View>
          <View className="add-station__way-text">
            <Text className="add-station__way-title">输入经纬度</Text>
            <Text className="add-station__way-sub">手动输入坐标</Text>
          </View>
          <Icon name="chevronRight" size={13} color="#9ca3af" />
        </View>
      </View>
    </View>
  )
}
