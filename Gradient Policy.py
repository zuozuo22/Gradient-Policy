#安装依赖
!pip install gym
!pip install paddlepaddle==1.6.3
!pip install parl==1.3.1

!pip list | grep paddlepaddle
!pip list | grep parl

#导入依赖
import os
import gym
import numpy as np

import paddle.fluid as fluid
import parl
from parl import layers
from parl.utils import logger

#设置超参数
LEARNING_RATE = 1e-3

#搭建Model、Algorithm、Agent架构,Agent把产生数据传给Algorithm,Algorithm根据Model的模型结构计算出Loss,使用优化器不断优化

class Model(parl.Model):
    def __init__(self, act_dim):
        act_dim = act_dim
        hid1_size = act_dim * 10

        self.fc1 = layers.fc(size=hid1_size, act='tanh')
        self.fc2 = layers.fc(size=act_dim, act='softmax')

    def forward(self, obs):  # 可直接用 model = Model(5); model(obs)调用
        out = self.fc1(obs)
        out = self.fc2(out)
        return out

#Algorithm 定义了具体的算法来更新前向网络(Model)，也就是通过定义损失函数来更新Model，和算法相关的计算都放在Algorithm中
class PolicyGradient(parl.Algorithm):
    def __init__(self, model, lr=None):
        self.model = model
        assert isinstance(lr, float)
        self.lr = lr

    def predict(self, obs):
        return self.model(obs)

    def learn(self, obs, action, reward):
        act_prob = self.model(obs)  # 获取输出动作概率
        # log_prob = layers.cross_entropy(act_prob, action) # 交叉熵
        log_prob = layers.reduce_sum(
            -1.0 * layers.log(act_prob) * layers.one_hot(
                action, act_prob.shape[1]),
            dim=1)
        cost = log_prob * reward
        cost = layers.reduce_mean(cost)

        optimizer = fluid.optimizer.Adam(self.lr)
        optimizer.minimize(cost)
        return cost

#Agent负责算法与环境的交互，在交互过程中把生成的数据提供给Algorithm来更新模型(Model)，数据的预处理流程也一般定义在这里。
class Agent(parl.Agent):
    def __init__(self, algorithm, obs_dim, act_dim):
        self.obs_dim = obs_dim
        self.act_dim = act_dim
        super(Agent, self).__init__(algorithm)

    def build_program(self):
        self.pred_program = fluid.Program()
        self.learn_program = fluid.Program()

        with fluid.program_guard(self.pred_program):  # 搭建计算图用于 预测动作，定义输入输出变量
            obs = layers.data(
                name='obs', shape=[self.obs_dim], dtype='float32')
            self.act_prob = self.alg.predict(obs)

        with fluid.program_guard(
                self.learn_program):  # 搭建计算图用于 更新policy网络，定义输入输出变量
            obs = layers.data(
                name='obs', shape=[self.obs_dim], dtype='float32')
            act = layers.data(name='act', shape=[1], dtype='int64')
            reward = layers.data(name='reward', shape=[], dtype='float32')
            self.cost = self.alg.learn(obs, act, reward)

    def sample(self, obs):
        obs = np.expand_dims(obs, axis=0)  # 增加一维维度
        act_prob = self.fluid_executor.run(
            self.pred_program,
            feed={'obs': obs.astype('float32')},
            fetch_list=[self.act_prob])[0]
        act_prob = np.squeeze(act_prob, axis=0)  # 减少一维维度
        act = np.random.choice(range(self.act_dim), p=act_prob)  # 根据动作概率选取动作
        return act

    def predict(self, obs):
        obs = np.expand_dims(obs, axis=0)
        act_prob = self.fluid_executor.run(
            self.pred_program,
            feed={'obs': obs.astype('float32')},
            fetch_list=[self.act_prob])[0]
        act_prob = np.squeeze(act_prob, axis=0)
        act = np.argmax(act_prob)  # 根据动作概率选择概率最高的动作
        return act

    def learn(self, obs, act, reward):
        act = np.expand_dims(act, axis=-1)
        feed = {
            'obs': obs.astype('float32'),
            'act': act.astype('int64'),
            'reward': reward.astype('float32')
        }
        cost = self.fluid_executor.run(
            self.learn_program, feed=feed, fetch_list=[self.cost])[0]
        return cost

#Training&&Test
def run_episode(env, agent):
    obs_list, action_list, reward_list = [], [], []
    obs = env.reset()
    while True:
        obs_list.append(obs)
        action = agent.sample(obs) # 采样动作
        action_list.append(action)

        obs, reward, done, info = env.step(action)
        reward_list.append(reward)

        if done:
            break
    return obs_list, action_list, reward_list

# 评估 agent, 跑 5 个episode，总reward求平均
def evaluate(env, agent, render=False):
    eval_reward = []
    for i in range(5):
        obs = env.reset()
        episode_reward = 0
        while True:
            action = agent.predict(obs) # 选取最优动作
            obs, reward, isOver, _ = env.step(action)
            episode_reward += reward
            if render:
                env.render()
            if isOver:
                break
        eval_reward.append(episode_reward)
    return np.mean(eval_reward)

# 创建环境和Agent，启动训练，保存模型
# 根据一个episode的每个step的reward列表，计算每一个Step的Gt
def calc_reward_to_go(reward_list, gamma=1.0):
    for i in range(len(reward_list) - 2, -1, -1):
        # G_t = r_t + γ·r_t+1 + ... = r_t + γ·G_t+1
        reward_list[i] += gamma * reward_list[i + 1]  # Gt
    return np.array(reward_list)


# 创建环境
env = gym.make('CartPole-v0')
obs_dim = env.observation_space.shape[0]
act_dim = env.action_space.n
logger.info('obs_dim {}, act_dim {}'.format(obs_dim, act_dim))

# 根据parl框架构建agent
model = Model(act_dim=act_dim)
alg = PolicyGradient(model, lr=LEARNING_RATE)
agent = Agent(alg, obs_dim=obs_dim, act_dim=act_dim)

for i in range(1000):
    obs_list, action_list, reward_list = run_episode(env, agent)
    if i % 10 == 0:
        logger.info("Episode {}, Reward Sum {}.".format(
            i, sum(reward_list)))

    batch_obs = np.array(obs_list)
    batch_action = np.array(action_list)
    batch_reward = calc_reward_to_go(reward_list)

    agent.learn(batch_obs, batch_action, batch_reward)
    if (i + 1) % 100 == 0:
        total_reward = evaluate(env, agent, render=False) # render=True 查看渲染效果，需要在本地运行，AIStudio无法显示
        logger.info('Test reward: {}'.format(total_reward))

agent.save('./model.ckpt')