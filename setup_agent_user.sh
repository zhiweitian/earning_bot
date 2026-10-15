#!/bin/bash
# 创建独立的 agent 用户，把项目搬到 /srv/earning（原路径留一个软链接，IDE 照常打开）。
# 用法：sudo ./setup_agent_user.sh    运行完需要注销重新登录一次，让你加入 earning 组。
# agent 用户能改：agent/ 下的代码和记忆、workspace/、logs/；
# 只能读：看守程序、goal.md、review.md、secrets.env、.git；完全看不到：.supervisor/ 和你的 home 目录。
set -e
OWNER=${SUDO_USER:?请用 sudo 运行}
SRC=$(cd "$(dirname "$0")" && pwd -P)
DST=/srv/earning

apt-get install -y python3-venv
id agent &>/dev/null || useradd -m -s /bin/bash agent
groupadd -f earning
usermod -aG earning agent
usermod -aG earning "$OWNER"

if [ "$SRC" != "$DST" ]; then
    mv "$SRC" "$DST"
    ln -s "$DST" "$SRC"
    chown -h "$OWNER":"$OWNER" "$SRC"
fi
cd "$DST"
mkdir -p workspace logs agent/skills .supervisor
[ -d .venv ] || python3 -m venv .venv
.venv/bin/pip install -q openai requests

# 默认归 agent，组内（包括你）可读写
chown -R agent:earning .
chmod -R g+rwX .
find . -path ./.git -prune -o -type d -exec chmod g+s {} +

# 受保护的部分归你，agent 只读或不可见
PROTECTED="supervisor.py review.py notify.py wechat.py start.sh stop.sh setup_agent_user.sh agent/goal.md"
[ -f agent/review.md ] && PROTECTED="$PROTECTED agent/review.md"
chown "$OWNER":earning . agent $PROTECTED secrets.env
chmod 644 $PROTECTED
chmod 755 *.sh
chmod 640 secrets.env
chmod 3775 . agent          # sticky：agent 不能删除或替换你的文件
chown -R "$OWNER":"$OWNER" .git .supervisor
chmod 700 .supervisor
chmod -R go-w .git

grep -q '^AGENT_USER=' secrets.env || echo 'AGENT_USER=agent' >> secrets.env
echo "$OWNER ALL=(agent) NOPASSWD: ALL" > /etc/sudoers.d/earning-agent
chmod 440 /etc/sudoers.d/earning-agent

echo "完成。项目在 $DST，$SRC 是指向它的软链接。"
echo "请注销并重新登录一次（让 $OWNER 加入 earning 组），然后再运行 ./start.sh"
