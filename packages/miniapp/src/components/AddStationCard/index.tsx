import { View, Text } from '@tarojs/components'
import { Icon } from '../Icon'
import './index.scss'

interface Props {
  onCatalog?: () => void
}

/** 添加站点卡。站点只能从公开电站目录添加，不提供自建。docs/01 §六 */
export function AddStationCard({ onCatalog }: Props) {
  return (
    <View className="add-station">
      <View className="add-station__head">
        <View className="add-station__plus">
          <Icon name="plus" size={20} color="#1677ff" />
        </View>
        <View className="add-station__text">
          <Text className="add-station__title">添加站点</Text>
          <Text className="add-station__sub">从全国近两万座光伏 / 风电场站里选择</Text>
        </View>
      </View>

      <View className="add-station__ways">
        <View className="add-station__way" onClick={onCatalog}>
          <View className="add-station__way-icon add-station__way-icon--blue">
            <Icon name="layers" size={16} color="#1677ff" />
          </View>
          <View className="add-station__way-text">
            <Text className="add-station__way-title">从公开电站选择</Text>
            <Text className="add-station__way-sub">按附近、名称或省市查找</Text>
          </View>
          <Icon name="chevronRight" size={13} color="#9ca3af" />
        </View>
      </View>
    </View>
  )
}
