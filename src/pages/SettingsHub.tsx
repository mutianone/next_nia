import { useState } from 'react';
import { useAuth } from '../auth/AuthContext';
import { PageHeading } from '../components/PageHeading';
import { Tabs, TabsList, TabsTrigger, TabsContent } from '../components/ui/tabs';
import { Appearance, type AppearanceProps } from './Appearance';
import { Settings } from './Settings';
import { UsageGuide } from '../components/UsageGuide';
import '../settings-hub.css';

export function SettingsHub({ theme, initialSection, onSaved }: { theme: AppearanceProps; initialSection?: 'appearance'; onSaved: () => void }) {
  const { user } = useAuth();
  const admin = user?.role === 'admin';
  const [section, setSection] = useState(initialSection || (admin ? 'configuration' : 'appearance'));
  return <div className="page-content settings-container settings-hub">
    <PageHeading title="系统设置" description="集中管理系统、外观偏好，查阅各功能操作步骤。" scene="settings"/>
    <Tabs value={section} onValueChange={setSection}>
      <TabsList className="settings-hub-tabs" aria-label="系统设置分类">
        {admin && <TabsTrigger value="configuration">系统管理</TabsTrigger>}
        <TabsTrigger value="appearance">外观主题</TabsTrigger>
        <TabsTrigger value="guide">使用引导</TabsTrigger>
      </TabsList>
      {admin && <TabsContent value="configuration" forceMount hidden={section !== 'configuration'}><div className="settings-admin-content"><Settings onSaved={onSaved}/></div></TabsContent>}
      <TabsContent value="appearance"><Appearance {...theme} embedded/></TabsContent>
      <TabsContent value="guide"><UsageGuide/></TabsContent>
    </Tabs>
  </div>;
}
