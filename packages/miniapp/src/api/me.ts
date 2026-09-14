/** 当前账号。docs/06 §五、docs/09 §4.3 */
import { api } from './index'

export const meApi = {
  /** 删除我的数据：本账号全部自建电站及其预警、发电记录、报告与预测留档，不可恢复 */
  deleteData: () => api.delete('/v1/me'),
}
