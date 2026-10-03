import type { Patient } from './types';

export interface IdentityCard {
  name: string; sex: Patient['sex']; birth_date: string; identity_number: string;
  ethnicity: string; address: string;
}
export interface IdentityReader { read(): Promise<IdentityCard> }

// Replace this adapter only after a real reader and its vendor interface are configured.
export const identityReader: IdentityReader = {
  async read() { throw new Error('身份证读卡器尚未接入，请手工填写人员资料。'); },
};
export const simulatedIdentityReader: IdentityReader = {
  async read() {
    return { name: '模拟读卡验收（虚构）', sex: 'female', birth_date: '1990-01-01',
      identity_number: '000000199001010006', ethnicity: '汉', address: '软件测试虚构地址' };
  },
};
